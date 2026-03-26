# scenarios/trackblazer.py
# Trackblazer (MANT) scenario-specific sub-routines.
# Inventory scanning, shop interaction, and item-use flows.

import utils.constants as constants
import utils.device_action_wrapper as device_action
import core.config as config
import core.bot as bot
from core.trackblazer_shop import get_shop_catalog
from core.state import (
    _save_training_scan_debug_image,
    _build_trackblazer_inventory_debug_entries,
    _build_trackblazer_shop_debug_entries,
    get_trackblazer_shop_coins,
    clear_runtime_ocr_debug,
    record_runtime_ocr_debug,
    snapshot_runtime_ocr_debug,
)
from core.ocr import extract_text
from utils.log import info, warning, debug
from utils.tools import get_secs, sleep
from utils.screenshot import enhance_image_for_ocr
from utils.shared import CleanDefaultDict
from PIL import Image
from time import time as _time
from pathlib import Path
from queue import Queue
import numpy as np
import cv2
import threading
import re

# Trackblazer item/shop assets are captured at the game's native screen
# resolution.  The bot applies a global template scale (currently 1.26x) to
# all templates by default, which makes these assets too large to match.
# This inverse factor cancels the global scale so they match 1:1.
_INVERSE_GLOBAL_SCALE = 1.0 / device_action.GLOBAL_TEMPLATE_SCALING
_INVENTORY_CONFIRM_USE_STATE_THRESHOLD = 0.98
_CONFIRM_USE_SCORE_THRESHOLD = 0.98
_CONFIRM_USE_AVAILABLE_BRIGHT_RATIO_THRESHOLD = 0.05
_STRICT_ITEM_THRESHOLDS = {
    "speed_ankle_weights": 0.88,
    "stamina_ankle_weights": 0.88,
    "power_ankle_weights": 0.88,
    "guts_ankle_weights": 0.88,
}
_ITEM_ROW_CLUSTER_TOLERANCE = 28
_ITEM_CLUSTER_PADDING = 10
_HELD_ROW_TOLERANCE = 26
_HELD_COUNT_REGION_WIDTH = 116
_HELD_COUNT_REGION_PADDING_Y = 4
_ITEM_ICON_SEARCH_WIDTH = 200
_SHOP_ICON_SEARCH_TOP_TRIM = 500
_SHOP_ICON_SEARCH_BOTTOM_TRIM = 330
_HELD_LABEL_SEARCH_X = 120
_HELD_LABEL_SEARCH_WIDTH = 180
_HELD_QUANTITY_OFFSET_FROM_ICON_RIGHT = 122
_HELD_QUANTITY_SLICE_WIDTH = 52
_HELD_QUANTITY_SLICE_HEIGHT = 35
_SHOP_SCROLLBAR_WINDOW_HALF_WIDTH = 3
_SHOP_SCROLLBAR_DARKNESS_DELTA = 18.0
_SHOP_SCROLLBAR_MIN_SEGMENT_HEIGHT = 8
_SHOP_SCROLLBAR_MIN_CONTRAST = 18.0
_SHOP_SCROLLBAR_EDGE_TOLERANCE = 12
_SHOP_SCROLLBAR_NON_SCROLLABLE_HEIGHT_RATIO = 0.9
_SHOP_SCROLLBAR_FRAME_INTERVAL_SECONDS = 0.2
_SHOP_SCROLLBAR_DRAG_DURATION_SECONDS = 3.2
_SHOP_SCROLLBAR_DRAG_END_PADDING = 10
_SHOP_SCROLLBAR_RESET_DURATION_SECONDS = 1.4
_SHOP_SCROLLBAR_ANALYSIS_WORKERS = 3
_SHOP_SCROLLBAR_SEEK_DURATION_SECONDS = 0.8
# Inventory scrollbar tuning — mirrors shop defaults; adjust if needed.
_INV_SCROLLBAR_WINDOW_HALF_WIDTH = 3
_INV_SCROLLBAR_DARKNESS_DELTA = 18.0
_INV_SCROLLBAR_MIN_SEGMENT_HEIGHT = 8
_INV_SCROLLBAR_MIN_CONTRAST = 18.0
_INV_SCROLLBAR_EDGE_TOLERANCE = 12
_INV_SCROLLBAR_NON_SCROLLABLE_HEIGHT_RATIO = 0.9
_INV_SCROLLBAR_RESET_DURATION_SECONDS = 1.4
_INV_SCROLLBAR_DRAG_END_PADDING = 10
_INV_SCROLLBAR_SEEK_DURATION_SECONDS = 0.8
_INV_SCROLLBAR_SETTLE_SECONDS = 0.35
_RIVAL_RACE_MATCH_THRESHOLD = 0.75
# When two family-variant scores are within this margin, use color
# disambiguation instead of trusting the raw template score.
_FAMILY_COLOR_TIEBREAK_MARGIN = 0.025
_CLEAT_HAMMER_HEAD_ROI = (0.48, 0.24, 0.24, 0.24)
_CLEAT_HAMMER_GOLD_HUE_RANGE = (12, 38)
_CLEAT_HAMMER_COLOR_MIN_DELTA = 0.04
# Megaphone spark disambiguation — the three megaphones differ in the number
# of yellow lightning-bolt sparks radiating from the horn.
# ROI covers the left half and top 80% of the icon (where sparks cluster).
_MEGAPHONE_SPARK_ROI = (0.0, 0.0, 0.50, 0.80)
# Expected white-spark-pixel ratios (sat<50, val>220) in the spark ROI.
_MEGAPHONE_SPARK_CENTERS = {
    "coaching_megaphone":   0.044,
    "motivating_megaphone": 0.060,
    "empowering_megaphone": 0.075,
}
# If the observed ratio is farther than this from all centres, abstain.
_MEGAPHONE_SPARK_MAX_DIST = 0.018
_ITEM_VARIANT_FAMILIES = (
    ("megaphone", (
        "motivating_megaphone",
        "coaching_megaphone",
        "empowering_megaphone",
    )),
    ("ankle_weights", (
        "speed_ankle_weights",
        "stamina_ankle_weights",
        "power_ankle_weights",
        "guts_ankle_weights",
    )),
    ("cleat_hammer", (
        "artisan_cleat_hammer",
        "master_cleat_hammer",
    )),
    ("manual", (
        "power_manual",
        "stamina_manual",
        "wit_manual",
    )),
    ("training_application", (
        "guts_training_application",
        "wit_training_application",
    )),
    ("vita", (
        "vita_20",
        "vita_65",
    )),
)
_ITEM_VARIANT_FAMILY_MAP = {
    item_name: family_items
    for _, family_items in _ITEM_VARIANT_FAMILIES
    for item_name in family_items
}


def _trackblazer_ui_region():
    return constants.GAME_WINDOW_BBOX


def _trackblazer_inventory_controls_region():
    return constants.MANT_SHOP_CONTROLS_BBOX


def _shop_confirm_template_keys():
    return ("shop_confirm", "shop_confirm_2", "inventory_use_training_items")


def _match_center_y(match):
    """Return the vertical center of a match rect (x, y, w, h)."""
    return match[1] + match[3] // 2


def _pair_item_to_increment(item_match, increment_matches, y_tolerance=30):
    """Find the increment (+) button on the same row as an item icon.

    The increment template also matches the decrement (-) button since they
    share a similar shape.  To disambiguate, this function first collects all
    matches on the same row (within *y_tolerance*) and then picks the
    **rightmost** one — the (+) button is always to the right of (-).

    Returns the matched increment rect (x, y, w, h) or None.
    """
    item_cy = _match_center_y(item_match)
    row_matches = []
    for inc_match in increment_matches:
        inc_cy = _match_center_y(inc_match)
        if abs(inc_cy - item_cy) <= y_tolerance:
            row_matches.append(inc_match)
    if not row_matches:
        return None
    # Rightmost match on the row is the (+) button.
    return max(row_matches, key=lambda m: m[0])


def _to_absolute_click_target(region_xywh, match):
    """Convert a region-relative match rect to an absolute screen click point.

    Returns (abs_x, abs_y) at the center of the match.
    """
    rx, ry = region_xywh[0], region_xywh[1]
    mx, my, mw, mh = match
    return (rx + mx + mw // 2, ry + my + mh // 2)


def _crop_from_match(screenshot, location, size):
    if screenshot is None or location is None or size is None:
        return None
    x, y = int(location[0]), int(location[1])
    w, h = int(size[0]), int(size[1])
    if w <= 0 or h <= 0:
        return None
    return screenshot[y:y + h, x:x + w].copy()


def _green_ratio(rgb_crop):
    if rgb_crop is None or getattr(rgb_crop, "size", 0) == 0:
        return 0.0
    red = rgb_crop[:, :, 0].astype(int)
    green = rgb_crop[:, :, 1].astype(int)
    blue = rgb_crop[:, :, 2].astype(int)
    green_mask = (green >= 110) & ((green - red) >= 18) & ((green - blue) >= 18)
    return round(float(green_mask.mean()), 4)


def _bright_ratio(rgb_crop, brightness_threshold=170):
    if rgb_crop is None or getattr(rgb_crop, "size", 0) == 0:
        return 0.0
    brightness = rgb_crop.mean(axis=2)
    return round(float((brightness >= brightness_threshold).mean()), 4)


def _item_threshold(item_name, threshold):
    return max(float(threshold), _STRICT_ITEM_THRESHOLDS.get(item_name, 0.0))


def _item_family_name(item_name):
    for family_name, items in _ITEM_VARIANT_FAMILIES:
        if item_name in items:
            return family_name
    return None


def _item_family_members(item_name):
    family_name = _item_family_name(item_name)
    if family_name is None:
        return (item_name,)
    for current_family_name, items in _ITEM_VARIANT_FAMILIES:
        if current_family_name == family_name:
            return items
    return (item_name,)


def _cluster_matches_by_row(candidates, tolerance=_ITEM_ROW_CLUSTER_TOLERANCE):
    clusters = []
    for candidate in sorted(candidates, key=lambda current: current["row_center_y"]):
        if not clusters:
            clusters.append([candidate])
            continue
        cluster = clusters[-1]
        anchor_y = sum(entry["row_center_y"] for entry in cluster) / len(cluster)
        if abs(candidate["row_center_y"] - anchor_y) <= tolerance:
            cluster.append(candidate)
        else:
            clusters.append([candidate])
    return clusters


def _cluster_bounds(cluster, screenshot_shape, padding=_ITEM_CLUSTER_PADDING):
    screenshot_h, screenshot_w = screenshot_shape[:2]
    left = max(0, min(entry["match"][0] for entry in cluster) - padding)
    top = max(0, min(entry["match"][1] for entry in cluster) - padding)
    right = min(screenshot_w, max(entry["match"][0] + entry["match"][2] for entry in cluster) + padding)
    bottom = min(screenshot_h, max(entry["match"][1] + entry["match"][3] for entry in cluster) + padding)
    return left, top, right, bottom


def _item_icon_search_crop(screenshot):
    """Return a left-column crop that contains the inventory item icons.

    The Trackblazer inventory layout places item icons in a narrow vertical
    strip on the left. Matching all item templates against the full inventory
    panel wastes most of the matchTemplate work. We keep OCR and increment
    pairing on the full screenshot, but crop the icon-matching search space.
    """
    if screenshot is None or getattr(screenshot, "size", 0) == 0:
        return None, 0
    screenshot_h, screenshot_w = screenshot.shape[:2]
    crop_width = max(1, min(int(_ITEM_ICON_SEARCH_WIDTH), int(screenshot_w)))
    return screenshot[0:screenshot_h, 0:crop_width].copy(), 0


def _shop_icon_search_crop(screenshot):
    """Return a cropped region for shop item icon matching.

    Like ``_item_icon_search_crop`` but also trims vertically — the shop
    header art and bottom confirm/back area never contain item icons, so
    excluding them shrinks the matchTemplate search space significantly.

    Returns ``(cropped_image, y_offset)`` where *y_offset* is the number of
    pixels trimmed from the top so callers can translate matches back to
    full-screenshot coordinates.
    """
    if screenshot is None or getattr(screenshot, "size", 0) == 0:
        return None, 0
    screenshot_h, screenshot_w = screenshot.shape[:2]
    crop_width = max(1, min(int(_ITEM_ICON_SEARCH_WIDTH), int(screenshot_w)))
    top = min(int(_SHOP_ICON_SEARCH_TOP_TRIM), screenshot_h - 1)
    bottom = max(top + 1, screenshot_h - int(_SHOP_ICON_SEARCH_BOTTOM_TRIM))
    return screenshot[top:bottom, 0:crop_width].copy(), top


def _crop_absolute_bbox_from_screenshot(screenshot, target_bbox, base_region_ltrb=None):
    """Crop an absolute bbox from a screenshot taken over *base_region_ltrb*."""
    if screenshot is None or getattr(screenshot, "size", 0) == 0:
        return None
    base_left, base_top, _base_right, _base_bottom = base_region_ltrb or _trackblazer_ui_region()
    target_left, target_top, target_right, target_bottom = [int(v) for v in target_bbox]
    rel_left = max(0, int(target_left - base_left))
    rel_top = max(0, int(target_top - base_top))
    rel_right = min(int(screenshot.shape[1]), int(target_right - base_left))
    rel_bottom = min(int(screenshot.shape[0]), int(target_bottom - base_top))
    if rel_right <= rel_left or rel_bottom <= rel_top:
        return None
    return screenshot[rel_top:rel_bottom, rel_left:rel_right].copy()


def _capture_live_trackblazer_ui_screenshot():
    """Force a fresh screenshot even while an ADB swipe is still in flight."""
    device_action.flush_screenshot_cache()
    return device_action.screenshot(region_ltrb=_trackblazer_ui_region())


def inspect_trackblazer_shop_scrollbar(screenshot=None):
    """Detect the Trackblazer shop scrollbar thumb and current scroll position."""
    ui_region = _trackblazer_ui_region()
    screenshot = screenshot if screenshot is not None else _capture_live_trackblazer_ui_screenshot()
    crop = _crop_absolute_bbox_from_screenshot(
        screenshot,
        constants.MANT_SHOP_SCROLLBAR_BBOX,
        base_region_ltrb=ui_region,
    )
    result = {
        "detected": False,
        "scrollable": False,
        "is_at_top": False,
        "is_at_bottom": False,
        "bbox": [int(v) for v in constants.MANT_SHOP_SCROLLBAR_BBOX],
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

    col_mean = gray.mean(axis=0)
    best_col = None
    for center in range(2, gray.shape[1] - 2):
        left = max(0, center - 2)
        right = min(gray.shape[1], center + 3)
        score = float(col_mean[left:right].mean())
        if best_col is None or score < best_col[0]:
            best_col = (score, center)
    track_center_x = int(best_col[1]) if best_col else int(gray.shape[1] // 2)
    left = max(0, track_center_x - _SHOP_SCROLLBAR_WINDOW_HALF_WIDTH)
    right = min(gray.shape[1], track_center_x + _SHOP_SCROLLBAR_WINDOW_HALF_WIDTH + 1)
    track = gray[:, left:right]
    row_mean = track.mean(axis=1)
    baseline = float(np.percentile(row_mean, 70))
    threshold = baseline - _SHOP_SCROLLBAR_DARKNESS_DELTA
    mask = row_mean < threshold

    segments = []
    start = None
    for idx, is_dark in enumerate(mask):
        if is_dark and start is None:
            start = idx
        elif not is_dark and start is not None:
            if idx - start >= _SHOP_SCROLLBAR_MIN_SEGMENT_HEIGHT:
                segments.append((start, idx - 1, float(row_mean[start:idx].mean())))
            start = None
    if start is not None and len(mask) - start >= _SHOP_SCROLLBAR_MIN_SEGMENT_HEIGHT:
        segments.append((start, len(mask) - 1, float(row_mean[start:].mean())))
    if not segments:
        return result

    thumb_top, thumb_bottom, thumb_darkness = min(segments, key=lambda entry: entry[2])
    thumb_height = int(thumb_bottom - thumb_top + 1)
    contrast = float(max(0.0, baseline - thumb_darkness))
    if contrast < _SHOP_SCROLLBAR_MIN_CONTRAST:
        return result

    track_height = int(gray.shape[0])
    travel_pixels = max(0, track_height - thumb_height)
    denominator = float(max(1, travel_pixels))
    position_ratio = min(1.0, max(0.0, float(thumb_top) / denominator))
    bbox_left, bbox_top, _bbox_right, _bbox_bottom = [int(v) for v in constants.MANT_SHOP_SCROLLBAR_BBOX]
    thumb_center_y = int(bbox_top + thumb_top + thumb_height // 2)
    track_center_abs_x = int(bbox_left + track_center_x)

    result.update({
        "detected": True,
        "scrollable": bool(thumb_height < int(track_height * _SHOP_SCROLLBAR_NON_SCROLLABLE_HEIGHT_RATIO)),
        "is_at_top": bool(thumb_top <= _SHOP_SCROLLBAR_EDGE_TOLERANCE),
        "is_at_bottom": bool((track_height - 1 - thumb_bottom) <= _SHOP_SCROLLBAR_EDGE_TOLERANCE),
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


def _drag_trackblazer_shop_scrollbar(scrollbar_state, edge="top", duration=_SHOP_SCROLLBAR_RESET_DURATION_SECONDS):
    """Drag the resolved shop scrollbar thumb to the requested edge."""
    edge_name = str(edge or "top").strip().lower()
    if edge_name not in ("top", "bottom"):
        raise ValueError(f"Unsupported shop scrollbar edge: {edge}")
    thumb_center = (scrollbar_state or {}).get("thumb_center")
    bbox = (scrollbar_state or {}).get("bbox") or [int(v) for v in constants.MANT_SHOP_SCROLLBAR_BBOX]
    track_center_x = int((scrollbar_state or {}).get("track_center_x") or 0)
    if not thumb_center or track_center_x <= 0:
        return {
            "direction": f"scrollbar_{edge_name}",
            "start": None,
            "end": None,
            "duration": float(duration),
            "settle_seconds": 0.0,
            "swiped": False,
        }
    start = (int(thumb_center[0]), int(thumb_center[1]))
    end_y = int(bbox[1] + 10) if edge_name == "top" else int(bbox[3] - _SHOP_SCROLLBAR_DRAG_END_PADDING)
    end = (track_center_x, end_y)
    swiped = bool(device_action.swipe(
        start,
        end,
        duration=duration,
        text=f"Trackblazer shop scrollbar drag to {edge_name}",
    ))
    return {
        "direction": f"scrollbar_{edge_name}",
        "start": [int(start[0]), int(start[1])],
        "end": [int(end[0]), int(end[1])],
        "duration": float(duration),
        "settle_seconds": 0.0,
        "swiped": swiped,
    }


def _drag_trackblazer_shop_scrollbar_to_ratio(scrollbar_state, position_ratio, duration=_SHOP_SCROLLBAR_SEEK_DURATION_SECONDS):
    """Drag the shop scrollbar thumb to an approximate position ratio."""
    thumb_center = (scrollbar_state or {}).get("thumb_center")
    bbox = (scrollbar_state or {}).get("bbox") or [int(v) for v in constants.MANT_SHOP_SCROLLBAR_BBOX]
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
        text=f"Trackblazer shop scrollbar seek ratio={clamped_ratio:.3f}",
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




def inspect_trackblazer_inventory_scrollbar(screenshot=None):
    """Detect the Trackblazer inventory scrollbar thumb and current scroll position.

    Mirrors :func:`inspect_trackblazer_shop_scrollbar` but uses
    ``MANT_INVENTORY_SCROLLBAR_BBOX`` as the crop region.
    """
    ui_region = _trackblazer_ui_region()
    screenshot = screenshot if screenshot is not None else _capture_live_trackblazer_ui_screenshot()
    crop = _crop_absolute_bbox_from_screenshot(
        screenshot,
        constants.MANT_INVENTORY_SCROLLBAR_BBOX,
        base_region_ltrb=ui_region,
    )
    result = {
        "detected": False,
        "scrollable": False,
        "is_at_top": False,
        "is_at_bottom": False,
        "bbox": [int(v) for v in constants.MANT_INVENTORY_SCROLLBAR_BBOX],
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

    col_mean = gray.mean(axis=0)
    best_col = None
    for center in range(2, gray.shape[1] - 2):
        left = max(0, center - 2)
        right = min(gray.shape[1], center + 3)
        score = float(col_mean[left:right].mean())
        if best_col is None or score < best_col[0]:
            best_col = (score, center)
    track_center_x = int(best_col[1]) if best_col else int(gray.shape[1] // 2)
    left = max(0, track_center_x - _INV_SCROLLBAR_WINDOW_HALF_WIDTH)
    right = min(gray.shape[1], track_center_x + _INV_SCROLLBAR_WINDOW_HALF_WIDTH + 1)
    track = gray[:, left:right]
    row_mean = track.mean(axis=1)
    baseline = float(np.percentile(row_mean, 70))
    threshold = baseline - _INV_SCROLLBAR_DARKNESS_DELTA
    mask = row_mean < threshold

    segments = []
    start = None
    for idx, is_dark in enumerate(mask):
        if is_dark and start is None:
            start = idx
        elif not is_dark and start is not None:
            if idx - start >= _INV_SCROLLBAR_MIN_SEGMENT_HEIGHT:
                segments.append((start, idx - 1, float(row_mean[start:idx].mean())))
            start = None
    if start is not None and len(mask) - start >= _INV_SCROLLBAR_MIN_SEGMENT_HEIGHT:
        segments.append((start, len(mask) - 1, float(row_mean[start:].mean())))
    if not segments:
        return result

    thumb_top, thumb_bottom, thumb_darkness = min(segments, key=lambda entry: entry[2])
    thumb_height = int(thumb_bottom - thumb_top + 1)
    contrast = float(max(0.0, baseline - thumb_darkness))
    if contrast < _INV_SCROLLBAR_MIN_CONTRAST:
        return result

    track_height = int(gray.shape[0])
    travel_pixels = max(0, track_height - thumb_height)
    denominator = float(max(1, travel_pixels))
    position_ratio = min(1.0, max(0.0, float(thumb_top) / denominator))
    bbox_left, bbox_top, _bbox_right, _bbox_bottom = [int(v) for v in constants.MANT_INVENTORY_SCROLLBAR_BBOX]
    thumb_center_y = int(bbox_top + thumb_top + thumb_height // 2)
    track_center_abs_x = int(bbox_left + track_center_x)

    result.update({
        "detected": True,
        "scrollable": bool(thumb_height < int(track_height * _INV_SCROLLBAR_NON_SCROLLABLE_HEIGHT_RATIO)),
        "is_at_top": bool(thumb_top <= _INV_SCROLLBAR_EDGE_TOLERANCE),
        "is_at_bottom": bool((track_height - 1 - thumb_bottom) <= _INV_SCROLLBAR_EDGE_TOLERANCE),
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


def _drag_trackblazer_inventory_scrollbar(scrollbar_state, edge="top", duration=_INV_SCROLLBAR_RESET_DURATION_SECONDS):
    """Drag the inventory scrollbar thumb to the requested edge."""
    edge_name = str(edge or "top").strip().lower()
    if edge_name not in ("top", "bottom"):
        raise ValueError(f"Unsupported inventory scrollbar edge: {edge}")
    thumb_center = (scrollbar_state or {}).get("thumb_center")
    bbox = (scrollbar_state or {}).get("bbox") or [int(v) for v in constants.MANT_INVENTORY_SCROLLBAR_BBOX]
    track_center_x = int((scrollbar_state or {}).get("track_center_x") or 0)
    if not thumb_center or track_center_x <= 0:
        return {
            "direction": f"scrollbar_{edge_name}",
            "start": None,
            "end": None,
            "duration": float(duration),
            "settle_seconds": 0.0,
            "swiped": False,
        }
    start = (int(thumb_center[0]), int(thumb_center[1]))
    end_y = int(bbox[1] + 10) if edge_name == "top" else int(bbox[3] - _INV_SCROLLBAR_DRAG_END_PADDING)
    end = (track_center_x, end_y)
    swiped = bool(device_action.swipe(
        start,
        end,
        duration=duration,
        text=f"Trackblazer inventory scrollbar drag to {edge_name}",
    ))
    return {
        "direction": f"scrollbar_{edge_name}",
        "start": [int(start[0]), int(start[1])],
        "end": [int(end[0]), int(end[1])],
        "duration": float(duration),
        "settle_seconds": 0.0,
        "swiped": swiped,
    }


def _drag_trackblazer_inventory_scrollbar_to_ratio(scrollbar_state, position_ratio, duration=_INV_SCROLLBAR_SEEK_DURATION_SECONDS):
    """Drag the inventory scrollbar thumb to an approximate position ratio."""
    thumb_center = (scrollbar_state or {}).get("thumb_center")
    bbox = (scrollbar_state or {}).get("bbox") or [int(v) for v in constants.MANT_INVENTORY_SCROLLBAR_BBOX]
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
        text=f"Trackblazer inventory scrollbar seek ratio={clamped_ratio:.3f}",
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


def _held_label_search_crop(screenshot):
    """Return the narrow vertical slice where the inventory 'Held' labels live."""
    if screenshot is None or getattr(screenshot, "size", 0) == 0:
        return None, 0
    screenshot_h, screenshot_w = screenshot.shape[:2]
    left = max(0, min(int(_HELD_LABEL_SEARCH_X), int(screenshot_w) - 1))
    right = max(left + 1, min(int(left + _HELD_LABEL_SEARCH_WIDTH), int(screenshot_w)))
    return screenshot[0:screenshot_h, left:right].copy(), left


def _held_quantity_crop_for_row(screenshot, item_match):
    """Crop the left held-count digit area for a resolved item row.

    Anchor the crop to the resolved item match so vertical scroll only affects
    Y and small horizontal drift between captures still keeps the quantity
    slice aligned. A fixed-X fallback is applied by the caller if needed.
    """
    if screenshot is None or getattr(screenshot, "size", 0) == 0:
        return None, None
    if item_match is None or len(item_match) != 4:
        return None, None
    screenshot_h, screenshot_w = screenshot.shape[:2]
    item_x, item_y, item_w, item_h = [int(v) for v in item_match]
    row_center_y = int(item_y + item_h // 2)
    left = int(item_x + item_w + _HELD_QUANTITY_OFFSET_FROM_ICON_RIGHT)
    left = max(0, min(left, int(screenshot_w) - 1))
    right = max(left + 1, min(int(left + _HELD_QUANTITY_SLICE_WIDTH), int(screenshot_w)))
    height = max(1, int(_HELD_QUANTITY_SLICE_HEIGHT))
    top = max(0, min(int(round(row_center_y - height / 2)), max(0, screenshot_h - height)))
    bottom = min(screenshot_h, top + height)
    crop = screenshot[top:bottom, left:right].copy()
    region = [int(left), int(top), int(max(0, right - left)), int(max(0, bottom - top))]
    return crop, region


def _held_quantity_crop_fixed_fallback(screenshot, row_center_y):
    """Fallback crop using the previously tuned fixed X slice."""
    if screenshot is None or getattr(screenshot, "size", 0) == 0:
        return None, None
    screenshot_h, screenshot_w = screenshot.shape[:2]
    left = max(0, min(238, int(screenshot_w) - 1))
    right = max(left + 1, min(int(left + _HELD_QUANTITY_SLICE_WIDTH), int(screenshot_w)))
    height = max(1, int(_HELD_QUANTITY_SLICE_HEIGHT))
    top = max(0, min(int(round(row_center_y - height / 2)), max(0, screenshot_h - height)))
    bottom = min(screenshot_h, top + height)
    crop = screenshot[top:bottom, left:right].copy()
    region = [int(left), int(top), int(max(0, right - left)), int(max(0, bottom - top))]
    return crop, region


def _ankle_weight_accent_score(bgr_crop, item_name):
    """Return the colour-accent score for a specific ankle-weight variant."""
    if bgr_crop is None or getattr(bgr_crop, "size", 0) == 0:
        return None
    # Focus on the core icon body so row/background noise does not dilute the
    # accent signal, especially for power which is mostly defined by the
    # absence of the other accent colours.
    icon_crop = _relative_crop(bgr_crop, 0.12, 0.12, 0.76, 0.76)
    if icon_crop is None or getattr(icon_crop, "size", 0) == 0:
        return None
    hsv = cv2.cvtColor(icon_crop, cv2.COLOR_RGB2HSV)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    sat_mask = s >= 50
    sat_count = int(sat_mask.sum())
    if sat_count < 20:
        return None
    h_sat = h[sat_mask].astype(int)
    bins = np.bincount(h_sat.flatten(), minlength=180)

    red_px = int(bins[0:12].sum() + bins[170:180].sum())
    orange_px = int(bins[12:22].sum())
    blue_px = int(bins[88:125].sum())
    pink_px = int(bins[148:170].sum())

    red_r = red_px / sat_count
    orange_r = orange_px / sat_count
    blue_r = blue_px / sat_count
    pink_r = pink_px / sat_count

    accent = {
        "speed_ankle_weights": blue_r,
        "guts_ankle_weights": pink_r,
        "stamina_ankle_weights": red_r,
        "power_ankle_weights": orange_r if (blue_r < 0.08 and pink_r < 0.12 and red_r < 0.08) else 0.0,
    }
    return {
        "score": float(accent.get(item_name, 0.0)),
        "ratios": {
            "red": float(red_r),
            "orange": float(orange_r),
            "blue": float(blue_r),
            "pink": float(pink_r),
        },
    }


def _ankle_weight_color_vote(screenshot, candidates):
    """Pick the best ankle-weight variant by accent-color ratios.

    *screenshot* is the full BGR screenshot (OpenCV order).
    *candidates* is a list of dicts with at least "item_name", "score", and "match".

    All four ankle-weight templates share the same orange/yellow background.
    The distinguishing accent colours (from template hue analysis) are:
        speed  → blue  (hue 88-125, ~25% of saturated pixels)
        guts   → pink  (hue 148-170, ~29%)
        stamina→ red   (hue 0-12 + 170-180, ~23%)
        power  → mostly orange (hue 12-22, ~84%, least accent)

    Returns the matching candidate, or None if no accent is clear enough.
    """
    if screenshot is None or getattr(screenshot, "size", 0) == 0:
        return None
    best_candidate = None
    best_accent = -1.0
    best_ratios = None
    for cand in candidates:
        match = cand.get("match")
        if not match:
            continue
        x, y, w, h = [int(v) for v in match]
        icon_crop = screenshot[y:y + h, x:x + w].copy()
        accent_result = _ankle_weight_accent_score(icon_crop, cand["item_name"])
        if accent_result is None:
            continue
        a = float(accent_result["score"])
        if a > best_accent:
            best_accent = a
            best_candidate = cand
            best_ratios = accent_result["ratios"]

    debug(
        "[TB_COLOR] ankle_weight ratios: "
        f"red={(best_ratios or {}).get('red', 0.0):.3f} "
        f"orange={(best_ratios or {}).get('orange', 0.0):.3f} "
        f"blue={(best_ratios or {}).get('blue', 0.0):.3f} "
        f"pink={(best_ratios or {}).get('pink', 0.0):.3f} "
        f"→ {best_candidate['item_name'] if best_candidate else '?'}"
    )
    if best_accent < 0.05:
        return None
    return best_candidate


def _relative_crop(image, rel_x, rel_y, rel_w, rel_h):
    if image is None or getattr(image, "size", 0) == 0:
        return None
    height, width = image.shape[:2]
    if height <= 0 or width <= 0:
        return None
    left = max(0, min(width - 1, int(round(width * rel_x))))
    top = max(0, min(height - 1, int(round(height * rel_y))))
    crop_w = max(1, int(round(width * rel_w)))
    crop_h = max(1, int(round(height * rel_h)))
    right = max(left + 1, min(width, left + crop_w))
    bottom = max(top + 1, min(height, top + crop_h))
    return image[top:bottom, left:right].copy()


def _cleat_hammer_color_vote(bgr_crop, candidates):
    """Pick artisan/master cleat by the hammer-head colour.

    Artisan has a silver/grey head; master has a gold/yellow head.
    """
    head_crop = _relative_crop(bgr_crop, *_CLEAT_HAMMER_HEAD_ROI)
    if head_crop is None or getattr(head_crop, "size", 0) == 0:
        return None
    hsv = cv2.cvtColor(head_crop, cv2.COLOR_RGB2HSV)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1]
    v = hsv[:, :, 2]

    gold_mask = (
        (s >= 50)
        & (v >= 80)
        & (h >= _CLEAT_HAMMER_GOLD_HUE_RANGE[0])
        & (h <= _CLEAT_HAMMER_GOLD_HUE_RANGE[1])
    )
    silver_mask = (s <= 45) & (v >= 90)

    gold_ratio = float(gold_mask.mean())
    silver_ratio = float(silver_mask.mean())
    scores = {
        "artisan_cleat_hammer": silver_ratio - gold_ratio,
        "master_cleat_hammer": gold_ratio - silver_ratio,
    }

    best_candidate = None
    best_score = float("-inf")
    for candidate in candidates:
        score = float(scores.get(candidate["item_name"], 0.0))
        if score > best_score:
            best_score = score
            best_candidate = candidate

    debug(
        f"[TB_COLOR] cleat_hammer ratios: gold={gold_ratio:.3f} silver={silver_ratio:.3f} "
        f"delta={gold_ratio - silver_ratio:.3f} "
        f"→ {best_candidate['item_name'] if best_candidate else '?'}"
    )
    if best_score < _CLEAT_HAMMER_COLOR_MIN_DELTA:
        return None
    return best_candidate


def _megaphone_spark_vote(icon_crop, candidates):
    """Pick megaphone variant by spark density in the upper-left region.

    The three megaphones share an identical body shape but differ in the
    number/density of yellow lightning-bolt sparks radiating from the horn:
        coaching    → fewest sparks  (lowest white-pixel ratio)
        motivating  → medium sparks
        empowering  → most sparks   (highest white-pixel ratio)

    *icon_crop* must be the matched icon region (not the padded cluster
    crop), so that surrounding inventory content does not pollute the
    measurement.

    Returns the candidate whose expected spark density best matches the
    observation, or ``None`` if the signal is too ambiguous.
    """
    spark_crop = _relative_crop(icon_crop, *_MEGAPHONE_SPARK_ROI)
    if spark_crop is None or getattr(spark_crop, "size", 0) == 0:
        return None
    hsv = cv2.cvtColor(spark_crop, cv2.COLOR_RGB2HSV)
    sat = hsv[:, :, 1]
    val = hsv[:, :, 2]

    # Spark highlights: low saturation + high value (white / near-white)
    spark_mask = (sat < 50) & (val > 220)
    spark_ratio = float(spark_mask.mean())

    best_candidate = None
    best_dist = float("inf")
    for cand in candidates:
        center = _MEGAPHONE_SPARK_CENTERS.get(cand["item_name"])
        if center is None:
            continue
        dist = abs(spark_ratio - center)
        if dist < best_dist:
            best_dist = dist
            best_candidate = cand

    debug(
        f"[TB_COLOR] megaphone spark_ratio={spark_ratio:.4f} "
        f"→ {best_candidate['item_name'] if best_candidate else '?'} "
        f"(dist={best_dist:.4f})"
    )
    if best_dist > _MEGAPHONE_SPARK_MAX_DIST:
        return None
    return best_candidate


def _resolve_family_cluster(cluster, family_items, screenshot, threshold):
    left, top, right, bottom = _cluster_bounds(cluster, screenshot.shape)
    cluster_crop = screenshot[top:bottom, left:right].copy()
    effective_scale = device_action._effective_template_scale(_INVERSE_GLOBAL_SCALE)
    passing_candidates = []
    for item_name in family_items:
        template_path = constants.TRACKBLAZER_ITEM_TEMPLATES.get(item_name)
        if not template_path:
            continue
        match = device_action.best_template_match(
            template_path,
            cluster_crop,
            template_scales=[effective_scale],
        )
        if match is None:
            continue
        score = float(match["score"])
        item_threshold = _item_threshold(item_name, threshold)
        if score < item_threshold:
            continue
        mx, my = match["location"]
        mw, mh = match["size"]
        candidate = {
            "item_name": item_name,
            "score": score,
            "threshold": item_threshold,
            "match": (int(left + mx), int(top + my), int(mw), int(mh)),
            "row_center_y": int(top + my + mh // 2),
        }
        passing_candidates.append(candidate)
    if not passing_candidates:
        return None
    passing_candidates.sort(key=lambda c: c["score"], reverse=True)
    best = passing_candidates[0]
    # Ankle-weight templates are nearly identical in shape — template scores
    # alone are unreliable.  Always use accent-color voting when any
    # candidate is an ankle weight, regardless of score margin.
    if any(c["item_name"].endswith("_ankle_weights") for c in passing_candidates):
        color_winner = _ankle_weight_color_vote(screenshot, passing_candidates)
        if color_winner is not None and color_winner["item_name"] != best["item_name"]:
            debug(
                f"[TB_FAMILY] Color tiebreak: {best['item_name']}({best['score']:.4f}) "
                f"→ {color_winner['item_name']}({color_winner['score']:.4f}) "
                f"margin={best['score'] - color_winner['score']:.4f}"
            )
            best = color_winner
    if any(c["item_name"].endswith("_cleat_hammer") for c in passing_candidates):
        color_winner = _cleat_hammer_color_vote(cluster_crop, passing_candidates)
        if color_winner is not None and color_winner["item_name"] != best["item_name"]:
            debug(
                f"[TB_FAMILY] Cleat color tiebreak: {best['item_name']}({best['score']:.4f}) "
                f"→ {color_winner['item_name']}({color_winner['score']:.4f}) "
                f"margin={best['score'] - color_winner['score']:.4f}"
            )
            best = color_winner
    if any(c["item_name"].endswith("_megaphone") for c in passing_candidates):
        # Extract the precise icon region from the screenshot (not the padded
        # cluster crop) so surrounding inventory content doesn't pollute the
        # spark-density measurement.
        bx, by, bw, bh = best["match"]
        icon_crop = screenshot[by:by + bh, bx:bx + bw].copy()
        spark_winner = _megaphone_spark_vote(icon_crop, passing_candidates)
        if spark_winner is not None and spark_winner["item_name"] != best["item_name"]:
            debug(
                f"[TB_FAMILY] Megaphone spark tiebreak: {best['item_name']}({best['score']:.4f}) "
                f"→ {spark_winner['item_name']}({spark_winner['score']:.4f}) "
                f"margin={best['score'] - spark_winner['score']:.4f}"
            )
            best = spark_winner
    return best


def _resolve_shop_family_rows(screenshot, family_items, threshold):
    if screenshot is None or not family_items:
        return []
    icon_screenshot, icon_offset_y = _shop_icon_search_crop(screenshot)
    if icon_screenshot is None or getattr(icon_screenshot, "size", 0) == 0:
        return []

    raw_matches = {}
    for item_name in family_items:
        template_path = constants.TRACKBLAZER_ITEM_TEMPLATES.get(item_name)
        if not template_path:
            raw_matches[item_name] = []
            continue
        item_threshold = _item_threshold(item_name, threshold)
        matches = device_action.match_template(
            template_path,
            icon_screenshot,
            item_threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
        raw_matches[item_name] = [
            (int(x), int(y + icon_offset_y), int(w), int(h))
            for (x, y, w, h) in matches
        ]

    family_candidates = []
    for item_name in family_items:
        for match in raw_matches.get(item_name, []):
            family_candidates.append(
                {
                    "item_name": item_name,
                    "match": match,
                    "row_center_y": int(_match_center_y(match)),
                }
            )
    if not family_candidates:
        return []

    resolved_rows = []
    for cluster in _cluster_matches_by_row(family_candidates):
        winner = _resolve_family_cluster(cluster, family_items, screenshot, threshold)
        if winner is None:
            continue
        resolved_rows.append(
            {
                "item_name": winner["item_name"],
                "match": list(winner["match"]),
                "row_center_y": int(winner["row_center_y"]),
            }
        )
    resolved_rows.sort(key=lambda row: row["row_center_y"])
    return resolved_rows


def _extract_inventory_quantity_from_crop(crop):
    if crop is None or getattr(crop, "size", 0) == 0:
        return {
            "raw_text": "",
            "held_quantity": None,
            "remaining_quantity": None,
        }
    pil = Image.fromarray(crop)
    # Try thresholds lazily — stop as soon as we get two digit groups
    # (the expected "X > Y" format).  Fall back to more aggressive
    # binarization only when the simpler pass fails.
    thresholds = [None, 220, 180]
    best_text = ""
    best_score = -1
    for binarize_threshold in thresholds:
        candidate = extract_text(
            enhance_image_for_ocr(pil, resize_factor=4, binarize_threshold=binarize_threshold),
            allowlist="0123456789>",
        )
        candidate = (candidate or "").strip()
        digits = re.findall(r"\d+", candidate)
        score = len(digits) * 10 + len(candidate)
        if score > best_score:
            best_text = candidate
            best_score = score
        if len(digits) >= 2:
            break
    digits = re.findall(r"\d+", best_text)
    held_quantity = int(digits[0]) if digits else None
    remaining_quantity = int(digits[1]) if len(digits) > 1 else None
    return {
        "raw_text": best_text,
        "held_quantity": held_quantity,
        "remaining_quantity": remaining_quantity,
    }


def _extract_inventory_held_quantity_from_crop(crop):
    """Read only the held quantity from the left side of the count strip."""
    if crop is None or getattr(crop, "size", 0) == 0:
        return {
            "raw_text": "",
            "held_quantity": None,
            "remaining_quantity": None,
        }
    pil = Image.fromarray(crop)
    thresholds = [None, 220, 180]
    best_text = ""
    best_score = -1
    for binarize_threshold in thresholds:
        candidate = extract_text(
            enhance_image_for_ocr(pil, resize_factor=4, binarize_threshold=binarize_threshold),
            allowlist="0123456789",
        )
        candidate = (candidate or "").strip()
        digits = re.findall(r"\d+", candidate)
        score = len(digits) * 10 + len(candidate)
        if score > best_score:
            best_text = candidate
            best_score = score
        if len(digits) >= 1:
            break
    digits = re.findall(r"\d+", best_text)
    held_quantity = int(digits[0]) if digits else None
    return {
        "raw_text": best_text,
        "held_quantity": held_quantity,
        "remaining_quantity": None,
    }


def _detect_inventory_held_rows(screenshot=None):
    if screenshot is None:
        screenshot = device_action.screenshot(region_ltrb=constants.GAME_WINDOW_BBOX)
    held_template = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("inventory_held")
    if not held_template:
        return []
    held_search_crop, held_search_offset_x = _held_label_search_crop(screenshot)
    matches = device_action.match_template(
        held_template,
        held_search_crop,
        threshold=0.95,
        template_scaling=_INVERSE_GLOBAL_SCALE,
    )
    rows = []
    for (x, y, w, h) in matches:
        x = int(x + held_search_offset_x)
        y = int(y)
        w = int(w)
        h = int(h)
        count_left = min(screenshot.shape[1], x + w + 4)
        count_right = min(screenshot.shape[1], count_left + _HELD_COUNT_REGION_WIDTH)
        count_top = max(0, y - _HELD_COUNT_REGION_PADDING_Y)
        count_bottom = min(screenshot.shape[0], y + h + _HELD_COUNT_REGION_PADDING_Y)
        count_crop = screenshot[count_top:count_bottom, count_left:count_right].copy()
        quantity = _extract_inventory_quantity_from_crop(count_crop)
        rows.append(
            {
                "match": [x, y, w, h],
                "row_center_y": int(y + h // 2),
                "count_region": [int(count_left), int(count_top), int(max(0, count_right - count_left)), int(max(0, count_bottom - count_top))],
                "raw_text": quantity["raw_text"],
                "held_quantity": quantity["held_quantity"],
                "remaining_quantity": quantity["remaining_quantity"],
            }
        )
    return sorted(rows, key=lambda r: r["row_center_y"])


def _pair_items_to_held_rows_by_rank(detected_items, held_rows):
    """Pair detected items to held rows by Y-rank order.

    Both lists must be in the same coordinate space.  Items and held rows
    are each sorted by center-Y, then paired 1-to-1 by position (first
    item row gets first held row, etc.).
    """
    sorted_items = sorted(detected_items, key=lambda d: d["row_center_y"])
    sorted_held = sorted(held_rows, key=lambda r: r["row_center_y"])
    pairs = {}
    for idx, item_entry in enumerate(sorted_items):
        if idx < len(sorted_held):
            pairs[item_entry["item_name"]] = sorted_held[idx]
        else:
            debug(
                f"[TB_INV] No held row for rank {idx} item "
                f"'{item_entry['item_name']}' (only {len(sorted_held)} held rows)"
            )
    return pairs


def _best_match_entry(template_path, region_ltrb=None, threshold=0.8, template_scaling=_INVERSE_GLOBAL_SCALE, screenshot=None):
    """Return a single best-match payload for a Trackblazer UI template."""
    region_ltrb = region_ltrb or _trackblazer_ui_region()
    if screenshot is None:
        screenshot = device_action.screenshot(region_ltrb=region_ltrb)
    search_image_path = ""
    if screenshot is not None and getattr(screenshot, "size", 0) != 0:
        search_image_path = _save_training_scan_debug_image(
            screenshot,
            "trackblazer_shop",
            "template_search_region",
        )
    best_match = device_action.best_template_match(
        template_path,
        screenshot,
        template_scales=[device_action._effective_template_scale(template_scaling)],
    )
    if best_match is None:
        return {
            "template": template_path,
            "threshold": threshold,
            "matched": False,
            "passed_threshold": False,
            "score": None,
            "location": None,
            "size": None,
            "click_target": None,
            "search_image_path": search_image_path,
            "region_ltrb": [int(v) for v in region_ltrb],
        }

    x, y = best_match["location"]
    w, h = best_match["size"]
    click_target = (
        int(region_ltrb[0] + x + w // 2),
        int(region_ltrb[1] + y + h // 2),
    )
    return {
        "template": template_path,
        "threshold": threshold,
        "matched": True,
        "passed_threshold": bool(best_match["score"] >= threshold),
        "score": round(best_match["score"], 4),
        "location": [int(x), int(y)],
        "size": [int(w), int(h)],
        "click_target": click_target,
        "search_image_path": search_image_path,
        "region_ltrb": [int(v) for v in region_ltrb],
    }


def detect_inventory_controls(threshold=0.6):
    """Detect bottom-row inventory controls on the current Trackblazer screen."""
    controls_region = _trackblazer_inventory_controls_region()
    screenshot = device_action.screenshot(region_ltrb=controls_region)
    controls = {}

    close_template = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_aftersale_close")
    if close_template:
        close_entry = _best_match_entry(close_template, region_ltrb=controls_region, threshold=threshold, screenshot=screenshot)
        close_entry["key"] = "close"
        controls["close"] = close_entry

    inventory_state_entries = {}
    for key in ("inventory_confirm_use_available", "inventory_confirm_use_unavailable"):
        template_path = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get(key)
        if not template_path:
            continue
        entry = _best_match_entry(
            template_path,
            region_ltrb=controls_region,
            threshold=_INVENTORY_CONFIRM_USE_STATE_THRESHOLD,
            screenshot=screenshot,
        )
        entry["key"] = key
        inventory_state_entries[key] = entry

    confirm_candidates = {}
    for key in (*_shop_confirm_template_keys(), "shop_aftersale_confirm_use_available", "shop_aftersale_confirm_use_unavailable"):
        template_path = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get(key)
        if not template_path:
            continue
        entry_threshold = _CONFIRM_USE_SCORE_THRESHOLD if "confirm_use" in key else threshold
        entry = _best_match_entry(template_path, region_ltrb=controls_region, threshold=entry_threshold, screenshot=screenshot)
        entry["key"] = key
        confirm_candidates[key] = entry
    confirm_candidates.update(inventory_state_entries)

    available_entry = confirm_candidates.get("shop_aftersale_confirm_use_available")
    unavailable_entry = confirm_candidates.get("shop_aftersale_confirm_use_unavailable")
    button_state = None
    state_reason = ""
    best_confirm = None

    inventory_available_entry = inventory_state_entries.get("inventory_confirm_use_available")
    inventory_unavailable_entry = inventory_state_entries.get("inventory_confirm_use_unavailable")
    passed_inventory_state_entries = [entry for entry in (inventory_available_entry, inventory_unavailable_entry) if entry and entry.get("passed_threshold")]

    if passed_inventory_state_entries:
        best_confirm = max(passed_inventory_state_entries, key=lambda entry: entry.get("score") or 0.0)
        crop = _crop_from_match(screenshot, best_confirm.get("location"), best_confirm.get("size"))
        best_confirm["green_ratio"] = _green_ratio(crop)
        best_confirm["bright_ratio"] = _bright_ratio(crop)
        if best_confirm.get("key") == "inventory_confirm_use_available":
            best_confirm["button_state"] = "available"
        else:
            best_confirm["button_state"] = "unavailable"
        best_confirm["button_state_reason"] = "inventory_specific_template"
    else:
        passed_state_candidates = [entry for entry in (available_entry, unavailable_entry) if entry and entry.get("passed_threshold")]
        if passed_state_candidates:
            best_state_candidate = max(passed_state_candidates, key=lambda entry: entry.get("score") or 0.0)
            crop = _crop_from_match(screenshot, best_state_candidate.get("location"), best_state_candidate.get("size"))
            green_ratio = _green_ratio(crop)
            bright_ratio = _bright_ratio(crop)
            button_state = "available" if bright_ratio >= _CONFIRM_USE_AVAILABLE_BRIGHT_RATIO_THRESHOLD else "unavailable"
            state_reason = "bright_ratio" if button_state == "available" else "low_bright_ratio"
            best_confirm = available_entry if button_state == "available" else unavailable_entry
            if best_confirm is None:
                best_confirm = best_state_candidate
            best_confirm["green_ratio"] = green_ratio
            best_confirm["bright_ratio"] = bright_ratio
            best_confirm["button_state"] = button_state
            best_confirm["button_state_reason"] = state_reason
            if available_entry is not None:
                available_entry["bright_ratio"] = bright_ratio
                available_entry["button_state"] = button_state
                available_entry["button_state_reason"] = state_reason
            if unavailable_entry is not None:
                unavailable_entry["bright_ratio"] = bright_ratio
                unavailable_entry["button_state"] = button_state
                unavailable_entry["button_state_reason"] = state_reason

    if best_confirm is None:
        generic_candidates = [
            confirm_candidates.get(key)
            for key in _shop_confirm_template_keys()
            if confirm_candidates.get(key)
        ]
        passed_generic_candidates = [entry for entry in generic_candidates if entry.get("passed_threshold")]
        if passed_generic_candidates:
            best_confirm = max(passed_generic_candidates, key=lambda entry: entry.get("score") or 0.0)

    if best_confirm:
        controls["confirm_use"] = best_confirm

    controls["confirm_candidates"] = confirm_candidates
    return controls


def detect_training_items_button(threshold=0.8):
    """Locate the lower-right lobby Training Items button.

    The same `training_items.png` asset can also match the small top label on
    the career HUD, so this prefers the lowest visible match on screen.
    """
    template_path = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("training_items_tab")
    if not template_path:
        return None

    region_ltrb = _trackblazer_ui_region()
    screenshot = device_action.screenshot(region_ltrb=region_ltrb)
    search_image_path = _save_training_scan_debug_image(
        screenshot,
        "trackblazer_inventory",
        "open_button_search_region",
    )
    matches = device_action.match_template(
        template_path,
        screenshot,
        threshold,
        template_scaling=_INVERSE_GLOBAL_SCALE,
    )
    best_match = device_action.best_template_match(
        template_path,
        screenshot,
        template_scales=[device_action._effective_template_scale(_INVERSE_GLOBAL_SCALE)],
    )
    if not matches:
        return {
            "template": template_path,
            "threshold": threshold,
            "matched": False,
            "passed_threshold": False,
            "score": round(best_match["score"], 4) if best_match and best_match.get("score") is not None else None,
            "match": None,
            "location": [int(v) for v in best_match["location"]] if best_match and best_match.get("location") is not None else None,
            "size": [int(v) for v in best_match["size"]] if best_match and best_match.get("size") is not None else None,
            "search_image_path": search_image_path,
            "click_target": None,
        }

    chosen_match = max(matches, key=lambda match: (match[1] + match[3] // 2, match[0] + match[2] // 2))
    x, y, w, h = chosen_match
    return {
        "template": template_path,
        "threshold": threshold,
        "matched": True,
        "passed_threshold": True,
        "score": round(best_match["score"], 4) if best_match and best_match.get("score") is not None else None,
        "match": [int(x), int(y), int(w), int(h)],
        "location": [int(x), int(y)],
        "size": [int(w), int(h)],
        "search_image_path": search_image_path,
        "click_target": (
            int(region_ltrb[0] + x + w // 2),
            int(region_ltrb[1] + y + h // 2),
        ),
    }


def detect_inventory_screen(threshold=0.8):
    """Check whether the Trackblazer inventory/item-use screen is open."""
    region_ltrb = _trackblazer_ui_region()
    screenshot = device_action.screenshot(region_ltrb=region_ltrb)
    checks = []
    for key in ("use_training_items", "use_back"):
        template_path = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get(key)
        if not template_path:
            continue
        entry = _best_match_entry(template_path, region_ltrb=region_ltrb, threshold=threshold, screenshot=screenshot)
        entry["key"] = key
        checks.append(entry)
        if entry["passed_threshold"]:
            return True, entry, checks
    controls = detect_inventory_controls(threshold=max(0.6, threshold - 0.2))
    for key in ("close", "confirm_use"):
        entry = controls.get(key)
        if not entry:
            continue
        checks.append(entry)
        if entry.get("passed_threshold"):
            return True, entry, checks
    return False, None, checks


def open_training_items_inventory(threshold=0.8, verify_threshold=0.8, skip_precheck=False):
    """Open the Trackblazer inventory screen from the lobby button."""
    t_total = _time()
    timing = {}

    if not skip_precheck:
        t0 = _time()
        already_open, verified_entry, checks = detect_inventory_screen(threshold=verify_threshold)
        timing["precheck"] = round(_time() - t0, 4)
        if already_open:
            timing["total"] = round(_time() - t_total, 4)
            info(f"[TB_INV] open timing: already_open — {timing}")
            return {
                "opened": True,
                "already_open": True,
                "clicked": False,
                "button": None,
                "verification": verified_entry,
                "verification_checks": checks,
                "timing": timing,
            }

    t0 = _time()
    button = detect_training_items_button(threshold=threshold)
    timing["detect_button"] = round(_time() - t0, 4)
    if not button or not button.get("matched") or not button.get("click_target"):
        timing["total"] = round(_time() - t_total, 4)
        info(f"[TB_INV] open timing: no_button — {timing}")
        return {
            "opened": False,
            "already_open": False,
            "clicked": False,
            "button": button,
            "verification": None,
            "verification_checks": [],
            "timing": timing,
        }

    t0 = _time()
    click_metrics = device_action.click_with_metrics(button["click_target"])
    clicked = bool(click_metrics.get("clicked"))
    timing["click_total"] = round(_time() - t0, 4)
    timing["click_breakdown"] = click_metrics

    t0 = _time()
    sleep(0.25)
    timing["sleep"] = round(_time() - t0, 4)

    t0 = _time()
    opened, verified_entry, verify_checks = detect_inventory_screen(threshold=verify_threshold)
    timing["verify"] = round(_time() - t0, 4)

    timing["total"] = round(_time() - t_total, 4)
    info(f"[TB_INV] open timing: {timing}")
    return {
        "opened": bool(clicked and opened),
        "already_open": False,
        "clicked": bool(clicked),
        "button": button,
        "verification": verified_entry,
        "verification_checks": verify_checks,
        "timing": timing,
    }


def close_training_items_inventory(threshold=0.8):
    """Close the Trackblazer inventory screen with Trackblazer-first fallbacks."""
    t_total = _time()
    timing = {}

    attempts = [
        ("shop_aftersale_close", constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_aftersale_close"), _INVERSE_GLOBAL_SCALE),
        ("use_back", constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("use_back"), _INVERSE_GLOBAL_SCALE),
        ("close_btn", "assets/buttons/close_btn.png", 1.0),
        ("back_btn", "assets/buttons/back_btn.png", 1.0),
    ]
    # Single screenshot for all close-button attempts
    region_ltrb = _trackblazer_ui_region()
    t0 = _time()
    close_screenshot = device_action.screenshot(region_ltrb=region_ltrb)
    timing["screenshot"] = round(_time() - t0, 4)

    attempt_entries = []
    last_clicked_entry = None
    last_click_metrics = None
    last_checks = []
    t0 = _time()
    for key, template_path, template_scaling in attempts:
        if not template_path:
            continue
        entry = _best_match_entry(
            template_path,
            region_ltrb=region_ltrb,
            threshold=threshold,
            template_scaling=template_scaling,
            screenshot=close_screenshot,
        )
        entry["key"] = key
        attempt_entries.append(entry)
        if not entry["passed_threshold"]:
            continue
        timing["match_attempts"] = round(_time() - t0, 4)
        timing["matched_key"] = key

        t0 = _time()
        click_metrics = device_action.click_with_metrics(entry["click_target"])
        clicked = bool(click_metrics.get("clicked"))
        timing["click_total"] = round(_time() - t0, 4)
        timing["click_breakdown"] = click_metrics
        last_clicked_entry = entry
        last_click_metrics = click_metrics

        t0 = _time()
        sleep(0.15)
        timing["sleep"] = round(_time() - t0, 4)

        # Lightweight verification: just check if the header is still visible
        t0 = _time()
        closed, checks = _wait_for_inventory_screen_to_close(
            max_wait_seconds=1.0,
            poll_seconds=0.15,
            threshold=threshold,
        )
        timing["verify"] = round(_time() - t0, 4)
        last_checks = checks

        if closed:
            timing["total"] = round(_time() - t_total, 4)
            info(f"[TB_INV] close timing: {timing}")
            return {
                "closed": True,
                "clicked": True,
                "attempt": entry,
                "attempts": attempt_entries,
                "verification_checks": checks,
                "timing": timing,
            }
        # Click landed but screen is still open — the template may have
        # matched a decorative element instead of the real button.  Take a
        # fresh screenshot and try the next template.
        info(f"[TB_INV] close: {key} matched (score={entry.get('score')}) but screen still open after click; trying next template.")
        device_action.flush_screenshot_cache()
        close_screenshot = device_action.screenshot(region_ltrb=region_ltrb)

    timing["match_attempts"] = round(_time() - t0, 4)
    timing["total"] = round(_time() - t_total, 4)
    if last_clicked_entry:
        info(f"[TB_INV] close timing: click_did_not_close — {timing}")
    else:
        info(f"[TB_INV] close timing: no_match — {timing}")
    return {
        "closed": False,
        "clicked": bool(last_click_metrics and last_click_metrics.get("clicked")),
        "attempt": last_clicked_entry,
        "attempts": attempt_entries,
        "verification_checks": last_checks,
        "timing": timing,
    }


def _wait_for_inventory_screen_to_close(max_wait_seconds=1.2, poll_seconds=0.2, threshold=0.75):
    """Poll briefly for the inventory screen to disappear after a close click."""
    checks = []
    deadline = _time() + max(0.0, float(max_wait_seconds))
    while _time() <= deadline:
        device_action.flush_screenshot_cache()
        still_open, _, checks = detect_inventory_screen(threshold=threshold)
        if not still_open:
            return True, checks
        sleep(max(0.05, float(poll_seconds)))
    device_action.flush_screenshot_cache()
    still_open, _, checks = detect_inventory_screen(threshold=threshold)
    return (not still_open), checks


def _wait_for_post_item_use_close_button(max_wait_seconds=10.0, poll_seconds=0.4, threshold=0.75):
    """Wait for the post-item-use close button and click it from lower UI regions."""
    t_total = _time()
    attempts = []
    polls = 0
    clicked = False
    closed = False
    click_result = None
    clicked_entry = None
    verification_checks = []
    saw_inventory_screen = False
    inventory_screen_reappeared_poll = None

    search_regions = (
        ("inventory_controls", _trackblazer_inventory_controls_region()),
        ("screen_bottom", constants.SCREEN_BOTTOM_BBOX),
        ("trackblazer_ui", _trackblazer_ui_region()),
    )
    template_attempts = (
        ("shop_aftersale_close", constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_aftersale_close"), _INVERSE_GLOBAL_SCALE),
        ("close_btn", "assets/buttons/close_btn.png", 1.0),
        ("use_back", constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("use_back"), _INVERSE_GLOBAL_SCALE),
        ("back_btn", "assets/buttons/back_btn.png", 1.0),
    )

    # Track positions that were clicked but didn't close the screen, so we
    # don't keep clicking the same decorative element on every poll.
    failed_click_counts = {}
    max_clicks_per_target = 2
    detect_controls_exhausted = False

    deadline = _time() + max(0.0, float(max_wait_seconds))
    while _time() <= deadline:
        polls += 1
        device_action.flush_screenshot_cache()

        inventory_open, _, screen_checks = detect_inventory_screen(threshold=max(0.7, threshold - 0.05))
        if screen_checks:
            verification_checks = screen_checks
        if inventory_open and not saw_inventory_screen:
            saw_inventory_screen = True
            inventory_screen_reappeared_poll = polls
            info("[TB_INV] Inventory screen reappeared after item-use confirm; waiting for close control.")

        if not saw_inventory_screen:
            sleep(max(0.1, float(poll_seconds)))
            continue

        # Try detect_inventory_controls first, but skip if a previous attempt
        # from this path already failed to close the screen.
        if not detect_controls_exhausted:
            controls = detect_inventory_controls(threshold=max(0.6, threshold - 0.1))
            close_entry = controls.get("close") or {}
            close_target = close_entry.get("click_target")
            if (
                close_entry.get("passed_threshold")
                and close_target
                and failed_click_counts.get(close_target, 0) < max_clicks_per_target
            ):
                clicked_entry = {**close_entry, "region_name": "inventory_controls_detect"}
                click_result = device_action.click_with_metrics(
                    close_target,
                    text="[TB_INV] Close inventory after item-use animation.",
                )
                clicked = bool(click_result.get("clicked"))
            else:
                clicked = False
        else:
            clicked = False

        if not clicked:
            for region_name, region_ltrb in search_regions:
                screenshot = device_action.screenshot(region_ltrb=region_ltrb)
                for key, template_path, template_scaling in template_attempts:
                    if not template_path:
                        continue
                    entry = _best_match_entry(
                        template_path,
                        region_ltrb=region_ltrb,
                        threshold=threshold,
                        template_scaling=template_scaling,
                        screenshot=screenshot,
                    )
                    entry["key"] = key
                    entry["region_name"] = region_name
                    attempts.append(entry)
                    click_target = entry.get("click_target")
                    if not entry.get("passed_threshold") or not click_target:
                        continue
                    if failed_click_counts.get(click_target, 0) >= max_clicks_per_target:
                        continue
                    clicked_entry = entry
                    click_result = device_action.click_with_metrics(
                        click_target,
                        text=f"[TB_INV] Close inventory via {key} in {region_name}.",
                    )
                    clicked = bool(click_result.get("clicked"))
                    break
                if clicked:
                    break

        if clicked:
            closed, verification_checks = _wait_for_inventory_screen_to_close(
                max_wait_seconds=1.2,
                poll_seconds=0.2,
                threshold=threshold,
            )
            if closed:
                break
            # This click position didn't close the screen — record it so we
            # can retry later without getting stuck on one target forever.
            if clicked_entry and clicked_entry.get("click_target"):
                target = clicked_entry["click_target"]
                failed_click_counts[target] = failed_click_counts.get(target, 0) + 1
            if not detect_controls_exhausted:
                detect_controls_exhausted = True
                info("[TB_INV] detect_inventory_controls close click did not close screen; falling back to template loop.")
            clicked = False

        sleep(max(0.1, float(poll_seconds)))

    return {
        "clicked": bool(click_result and click_result.get("clicked")),
        "closed": bool(closed),
        "clicked_entry": clicked_entry,
        "click_result": click_result,
        "attempts": attempts,
        "polls": polls,
        "verification_checks": verification_checks,
        "saw_inventory_screen": bool(saw_inventory_screen),
        "inventory_screen_reappeared_poll": inventory_screen_reappeared_poll,
        "timing_total": round(_time() - t_total, 3),
    }


def _wait_for_post_shop_close_button(max_wait_seconds=4.0, poll_seconds=0.3, threshold=0.75):
    """Wait for the exchange-complete close button after confirming shop purchases."""
    t_total = _time()
    attempts = []
    polls = 0
    clicked = False
    closed = False
    click_result = None
    clicked_entry = None
    verification_checks = []

    search_regions = (
        ("inventory_controls", _trackblazer_inventory_controls_region()),
        ("screen_bottom", constants.SCREEN_BOTTOM_BBOX),
        ("trackblazer_ui", _trackblazer_ui_region()),
    )
    template_attempts = (
        ("shop_aftersale_close", constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_aftersale_close"), _INVERSE_GLOBAL_SCALE),
        ("close_btn", "assets/buttons/close_btn.png", 1.0),
        ("back_btn", "assets/buttons/back_btn.png", 1.0),
        ("use_back", constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("use_back"), _INVERSE_GLOBAL_SCALE),
    )

    failed_click_positions = set()
    deadline = _time() + max(0.0, float(max_wait_seconds))
    while _time() <= deadline:
        polls += 1
        device_action.flush_screenshot_cache()
        controls = detect_inventory_controls(threshold=max(0.6, threshold - 0.1))
        close_entry = controls.get("close") or {}
        close_target = close_entry.get("click_target")
        if (
            close_entry.get("passed_threshold")
            and close_target
            and close_target not in failed_click_positions
        ):
            clicked_entry = {**close_entry, "region_name": "inventory_controls_detect"}
            click_result = device_action.click_with_metrics(
                close_target,
                text="[TB_SHOP] Dismiss exchange-complete dialog.",
            )
            clicked = bool(click_result.get("clicked"))
        else:
            clicked = False

        if not clicked:
            for region_name, region_ltrb in search_regions:
                screenshot = device_action.screenshot(region_ltrb=region_ltrb)
                for key, template_path, template_scaling in template_attempts:
                    if not template_path:
                        continue
                    entry = _best_match_entry(
                        template_path,
                        region_ltrb=region_ltrb,
                        threshold=threshold,
                        template_scaling=template_scaling,
                        screenshot=screenshot,
                    )
                    entry["key"] = key
                    entry["region_name"] = region_name
                    attempts.append(entry)
                    click_target = entry.get("click_target")
                    if not entry.get("passed_threshold") or not click_target:
                        continue
                    if click_target in failed_click_positions:
                        continue
                    clicked_entry = entry
                    click_result = device_action.click_with_metrics(
                        click_target,
                        text=f"[TB_SHOP] Close shop dialog via {key} in {region_name}.",
                    )
                    clicked = bool(click_result.get("clicked"))
                    break
                if clicked:
                    break

        if clicked:
            sleep(0.2)
            still_open, _, verification_checks = detect_shop_screen(threshold=threshold)
            closed = not still_open
            if closed:
                break
            if clicked_entry and clicked_entry.get("click_target"):
                failed_click_positions.add(clicked_entry["click_target"])
            clicked = False

        sleep(max(0.1, float(poll_seconds)))

    return {
        "clicked": bool(click_result and click_result.get("clicked")),
        "closed": bool(closed),
        "clicked_entry": clicked_entry,
        "click_result": click_result,
        "attempts": attempts,
        "polls": polls,
        "verification_checks": verification_checks,
        "timing_total": round(_time() - t_total, 3),
    }


def _scan_inventory_page(threshold=0.8):
    """Scan a single visible page of the inventory for item icons.

    This is the low-level single-screenshot scan.  It does NOT scroll or
    interact with the screen — the caller is responsible for ensuring the
    desired scroll position is already settled.

    Returns ``(inventory_page, page_timing)`` where *inventory_page* is a
    :class:`CleanDefaultDict` keyed by item name (same structure as the
    public :func:`scan_training_items_inventory`) and *page_timing* is a
    dict of timing metrics for this single page.
    """
    t_total = _time()
    region_xywh = constants.MANT_INVENTORY_ITEMS_REGION
    screenshot = device_action.screenshot(region_xywh=region_xywh)
    icon_screenshot, icon_offset_x = _item_icon_search_crop(screenshot)
    icon_search_image_path = _save_training_scan_debug_image(
        icon_screenshot,
        "trackblazer_inventory",
        "item_icon_search_region",
    )

    # Find all increment buttons in the region first so we can pair them.
    # Use inverse global scale since these assets are at native resolution.
    increment_template = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get(
        "shop_aftersale_confirm_use_increment_item"
    )
    increment_matches = []
    if increment_template:
        increment_matches = device_action.match_template(
            increment_template, screenshot, threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
    increment_matches = [
        (int(x), int(y), int(w), int(h))
        for (x, y, w, h) in increment_matches
    ]

    t0 = _time()
    raw_matches = {}
    for item_name, template_path in constants.TRACKBLAZER_ITEM_TEMPLATES.items():
        item_threshold = _item_threshold(item_name, threshold)
        matches = device_action.match_template(
            template_path, icon_screenshot, item_threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
        raw_matches[item_name] = [
            (int(x + icon_offset_x), int(y), int(w), int(h))
            for (x, y, w, h) in matches
        ]
    t_templates = _time() - t0

    t0 = _time()
    families_resolved = 0
    families_skipped = 0
    resolved_matches = {item_name: list(matches) for item_name, matches in raw_matches.items()}
    for family_name, family_items in _ITEM_VARIANT_FAMILIES:
        family_candidates = []
        for item_name in family_items:
            for match in raw_matches.get(item_name, []):
                family_candidates.append(
                    {
                        "item_name": item_name,
                        "match": match,
                        "row_center_y": int(_match_center_y(match)),
                    }
                )
        if not family_candidates:
            continue
        for item_name in family_items:
            resolved_matches[item_name] = []
        for cluster in _cluster_matches_by_row(family_candidates):
            winner = _resolve_family_cluster(cluster, family_items, screenshot, threshold)
            if winner is None:
                debug(
                    f"[TB_INV] No winner resolved for family '{family_name}' "
                    f"cluster at rows {[entry['row_center_y'] for entry in cluster]}"
                )
                continue
            resolved_matches[winner["item_name"]].append(winner["match"])
            families_resolved += 1
    t_families = _time() - t0

    # Build a list of detected items with their row center.
    detected_items_for_pairing = []
    for item_name, matches in resolved_matches.items():
        if matches:
            detected_items_for_pairing.append({
                "item_name": item_name,
                "row_center_y": int(_match_center_y(matches[0])),
            })

    # Read held quantities directly from the resolved item rows instead of
    # matching the separate "Held" label and pairing by rank.
    t0 = _time()
    held_quantities = {}
    for item_entry in detected_items_for_pairing:
        quantity_crop, quantity_region = _held_quantity_crop_for_row(
            screenshot,
            resolved_matches.get(item_entry["item_name"], [None])[0],
        )
        quantity = _extract_inventory_held_quantity_from_crop(quantity_crop)
        if quantity.get("held_quantity") is None:
            fallback_crop, fallback_region = _held_quantity_crop_fixed_fallback(
                screenshot,
                item_entry["row_center_y"],
            )
            fallback_quantity = _extract_inventory_held_quantity_from_crop(fallback_crop)
            if fallback_quantity.get("held_quantity") is not None:
                quantity = fallback_quantity
                quantity_region = fallback_region
        held_quantities[item_entry["item_name"]] = {
            "held_quantity": quantity.get("held_quantity"),
            "remaining_quantity": None,
            "raw_text": quantity.get("raw_text", ""),
            "count_region": quantity_region,
        }
    t_held = _time() - t0

    inventory = CleanDefaultDict()
    for item_name, template_path in constants.TRACKBLAZER_ITEM_TEMPLATES.items():
        item_threshold = _item_threshold(item_name, threshold)
        matches = resolved_matches.get(item_name, [])
        category = constants.TRACKBLAZER_ITEM_CATEGORIES.get(item_name, "unknown")

        increment_target = None
        increment_match_raw = None
        row_center_y = None
        held_quantity = None
        remaining_quantity = None
        quantity_text = ""
        quantity_region = None
        if matches:
            # Use the first (topmost) match for pairing.
            paired = _pair_item_to_increment(matches[0], increment_matches)
            row_center_y = int(_match_center_y(matches[0]))
            if paired:
                increment_target = _to_absolute_click_target(region_xywh, paired)
                increment_target = (int(increment_target[0]), int(increment_target[1]))
                increment_match_raw = [int(v) for v in paired]
            quantity_entry = held_quantities.get(item_name)
            if quantity_entry is not None:
                held_quantity = quantity_entry.get("held_quantity")
                remaining_quantity = quantity_entry.get("remaining_quantity")
                quantity_text = quantity_entry.get("raw_text", "")
                quantity_region = quantity_entry.get("count_region")

        inventory[item_name] = {
            "detected": len(matches) > 0,
            "category": category,
            "threshold": item_threshold,
            "match_count": len(matches),
            "matches": [[int(v) for v in m] for m in matches],
            "search_image_path": icon_search_image_path,
            "increment_target": increment_target,
            "increment_match": increment_match_raw,
            "row_center_y": row_center_y,
            "held_quantity": held_quantity,
            "remaining_quantity": remaining_quantity,
            "quantity_text": quantity_text,
            "quantity_region": quantity_region,
        }

    t_total_elapsed = _time() - t_total
    page_timing = {
        "total": round(t_total_elapsed, 4),
        "held_ocr": round(t_held, 4),
        "templates": round(t_templates, 4),
        "families": round(t_families, 4),
        "families_resolved": families_resolved,
        "families_skipped": families_skipped,
        "items_detected": len(detected_items_for_pairing),
        "quantity_reads": len(held_quantities),
    }
    return inventory, page_timing


def _merge_inventory_pages(base, overlay):
    """Merge two single-page inventory dicts.

    *overlay* wins for increment targets (since it reflects the current
    scroll position).  Detection and held-quantity are unioned — if either
    page saw the item, it counts as detected, and the higher held_quantity
    is kept (they should agree, but max is safer).

    Each merged entry gets a ``scroll_page`` field: ``"top"`` if the item
    was only on *base*, ``"bottom"`` if only on *overlay*, or ``"both"``
    if visible on both pages.
    """
    merged = CleanDefaultDict()
    all_keys = set(base.keys()) | set(overlay.keys())
    for item_name in all_keys:
        if item_name.startswith("_"):
            continue
        b = base.get(item_name) or {}
        o = overlay.get(item_name) or {}
        b_detected = b.get("detected", False)
        o_detected = o.get("detected", False)

        if o_detected:
            # Overlay (bottom page) is current view — prefer its targets.
            entry = dict(o)
            if b_detected and not o_detected:
                pass  # won't reach here
            if b_detected:
                entry["scroll_page"] = "both"
            else:
                entry["scroll_page"] = "bottom"
            # Take the higher held quantity in case OCR disagreed.
            b_qty = b.get("held_quantity")
            o_qty = o.get("held_quantity")
            if b_qty is not None and o_qty is not None:
                entry["held_quantity"] = max(b_qty, o_qty)
            elif b_qty is not None:
                entry["held_quantity"] = b_qty
        elif b_detected:
            # Only on top page — keep detection + quantity but mark targets
            # as stale (current scroll is at bottom).
            entry = dict(b)
            entry["scroll_page"] = "top"
            entry["increment_target_stale"] = True
        else:
            # Not detected on either page.
            entry = dict(o if o else b)
            entry["scroll_page"] = None
        merged[item_name] = entry
    return merged


def scan_training_items_inventory(threshold=0.8):
    """Scan the inventory for owned training item icons, scrolling if needed.

    Checks the inventory scrollbar after the first page scan.  If the list
    is scrollable, resets to the top, scans the top page, scrolls to the
    bottom, scans again, and merges the results.

    Returns a dict keyed by item name (see :func:`_scan_inventory_page`
    for per-item fields).  Items only visible on a non-current scroll page
    have ``scroll_page`` set to ``"top"`` and ``increment_target_stale``
    set to ``True``.
    """
    t_total = _time()

    # Check scrollbar before scanning to decide on multi-page.
    scrollbar_pre = inspect_trackblazer_inventory_scrollbar()
    needs_scroll = scrollbar_pre.get("detected") and scrollbar_pre.get("scrollable")

    scroll_flow = {
        "scrollbar_detected": scrollbar_pre.get("detected", False),
        "scrollable": needs_scroll,
        "pages_scanned": 1,
        "reset_swipe": None,
        "forward_swipe": None,
        "scrollbar_pre": scrollbar_pre,
        "scrollbar_post": None,
    }

    if needs_scroll and not scrollbar_pre.get("is_at_top"):
        scroll_flow["reset_swipe"] = _drag_trackblazer_inventory_scrollbar(
            scrollbar_pre, edge="top",
        )
        sleep(_INV_SCROLLBAR_SETTLE_SECONDS)
        device_action.flush_screenshot_cache()

    # -- Page 1 (top / only page) --
    page1, page1_timing = _scan_inventory_page(threshold=threshold)

    if not needs_scroll:
        # Single-page inventory — no scrolling needed.
        t_total_elapsed = _time() - t_total
        page1_timing["total"] = round(t_total_elapsed, 4)
        page1_timing["scroll"] = scroll_flow
        info(
            f"[TB_INV] scan timing: total={t_total_elapsed:.2f}s "
            f"held_ocr={page1_timing.get('held_ocr', 0):.2f}s "
            f"templates={page1_timing.get('templates', 0):.2f}s "
            f"families={page1_timing.get('families', 0):.2f}s "
            f"(resolved={page1_timing.get('families_resolved', 0)} "
            f"skipped={page1_timing.get('families_skipped', 0)}) "
            f"items_detected={page1_timing.get('items_detected', 0)} "
            f"quantity_reads={page1_timing.get('quantity_reads', 0)}"
        )
        page1["_timing"] = page1_timing
        return page1

    # -- Scroll to bottom for page 2 --
    scrollbar_mid = inspect_trackblazer_inventory_scrollbar()
    scroll_flow["forward_swipe"] = _drag_trackblazer_inventory_scrollbar(
        scrollbar_mid, edge="bottom",
    )
    sleep(_INV_SCROLLBAR_SETTLE_SECONDS)
    device_action.flush_screenshot_cache()

    page2, page2_timing = _scan_inventory_page(threshold=threshold)
    scroll_flow["pages_scanned"] = 2

    scrollbar_post = inspect_trackblazer_inventory_scrollbar()
    scroll_flow["scrollbar_post"] = scrollbar_post

    # -- Merge pages --
    inventory = _merge_inventory_pages(page1, page2)

    t_total_elapsed = _time() - t_total
    scan_timing = {
        "total": round(t_total_elapsed, 4),
        "held_ocr": round(page1_timing.get("held_ocr", 0) + page2_timing.get("held_ocr", 0), 4),
        "templates": round(page1_timing.get("templates", 0) + page2_timing.get("templates", 0), 4),
        "families": round(page1_timing.get("families", 0) + page2_timing.get("families", 0), 4),
        "families_resolved": page1_timing.get("families_resolved", 0) + page2_timing.get("families_resolved", 0),
        "families_skipped": page1_timing.get("families_skipped", 0) + page2_timing.get("families_skipped", 0),
        "items_detected": sum(
            1 for k, v in inventory.items()
            if not k.startswith("_") and v.get("detected")
        ),
        "quantity_reads": page1_timing.get("quantity_reads", 0) + page2_timing.get("quantity_reads", 0),
        "page1_timing": page1_timing,
        "page2_timing": page2_timing,
        "scroll": scroll_flow,
    }
    info(
        f"[TB_INV] scan timing (scrolled): total={t_total_elapsed:.2f}s "
        f"pages=2 items_detected={scan_timing['items_detected']} "
        f"held_ocr={scan_timing['held_ocr']:.2f}s "
        f"templates={scan_timing['templates']:.2f}s"
    )
    inventory["_timing"] = scan_timing
    return inventory


def build_inventory_summary(inventory):
    """Reduce a full inventory scan to a compact summary dict.

    Returns:
      - "items_detected": list of detected item names
      - "by_category": dict mapping category -> list of item names
      - "total_detected": int
      - "actionable_items": list of item names that have a paired increment target
    """
    summary = {
        "items_detected": [],
        "by_category": {},
        "total_detected": 0,
        "actionable_items": [],
        "held_quantities": {},
    }
    for item_name, data in inventory.items():
        if not data.get("detected"):
            continue
        summary["items_detected"].append(item_name)
        summary["total_detected"] += 1
        cat = data.get("category", "unknown")
        summary["by_category"].setdefault(cat, []).append(item_name)
        if data.get("increment_target"):
            summary["actionable_items"].append(item_name)
        if data.get("held_quantity") is not None:
            summary["held_quantities"][item_name] = data.get("held_quantity")
    return summary


def scan_trackblazer_shop_inventory(
    threshold=0.8,
    checkbox_threshold=0.8,
    confirm_threshold=0.8,
    screenshot=None,
    save_debug_image=True,
):
    """Scan the currently visible Trackblazer shop item rows without clicking.

    This is the first page-only framework. Scrolling will layer on top later.
    It detects visible item icons, resolves same-family variants row-by-row,
    pairs each row to the unchecked purchase checkbox on the right, and reads
    the current confirm button state. Costs are intentionally deferred.
    """
    t_total = _time()
    region_ltrb = _trackblazer_ui_region()
    t0 = _time()
    screenshot = screenshot if screenshot is not None else device_action.screenshot(region_ltrb=region_ltrb)
    t_capture = _time() - t0
    icon_screenshot, icon_offset_y = _shop_icon_search_crop(screenshot)
    icon_search_image_path = ""
    if save_debug_image:
        icon_search_image_path = _save_training_scan_debug_image(
            icon_screenshot,
            "trackblazer_shop",
            "item_icon_search_region",
        )

    raw_matches = {}
    t0 = _time()
    for item_name, template_path in constants.TRACKBLAZER_ITEM_TEMPLATES.items():
        item_threshold = _item_threshold(item_name, threshold)
        matches = device_action.match_template(
            template_path,
            icon_screenshot,
            item_threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
        raw_matches[item_name] = [
            (int(x), int(y + icon_offset_y), int(w), int(h))
            for (x, y, w, h) in matches
        ]
    t_templates = _time() - t0

    t0 = _time()
    resolved_matches = {item_name: list(matches) for item_name, matches in raw_matches.items()}
    families_resolved = 0
    families_skipped = 0
    for _, family_items in _ITEM_VARIANT_FAMILIES:
        family_candidates = []
        for item_name in family_items:
            for match in raw_matches.get(item_name, []):
                family_candidates.append(
                    {
                        "item_name": item_name,
                        "match": match,
                        "row_center_y": int(_match_center_y(match)),
                    }
                )
        if not family_candidates:
            continue
        for item_name in family_items:
            resolved_matches[item_name] = []
        for cluster in _cluster_matches_by_row(family_candidates):
            winner = _resolve_family_cluster(cluster, family_items, screenshot, threshold)
            if winner is None:
                continue
            resolved_matches[winner["item_name"]].append(winner["match"])
            families_resolved += 1
    t_families = _time() - t0

    t0 = _time()
    checkbox_template = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("select_unchecked")
    checkbox_matches = []
    if checkbox_template:
        checkbox_matches = device_action.match_template(
            checkbox_template,
            screenshot,
            checkbox_threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
    checkbox_matches = [
        (int(x), int(y), int(w), int(h))
        for (x, y, w, h) in checkbox_matches
        if int(x) >= 560 and int(y) >= 450 and int(y) <= 1100
    ]
    purchased_template = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_item_purchased")
    purchased_matches = []
    if purchased_template:
        purchased_matches = device_action.match_template(
            purchased_template,
            screenshot,
            checkbox_threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
    purchased_matches = [
        (int(x), int(y), int(w), int(h))
        for (x, y, w, h) in purchased_matches
        if int(x) >= 500 and int(y) >= 400 and int(y) <= 1100
    ]
    t_checkboxes = _time() - t0

    t0 = _time()
    confirm_entry = None
    confirm_candidates = []
    for confirm_key in _shop_confirm_template_keys():
        confirm_template = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get(confirm_key)
        if not confirm_template:
            continue
        entry = _best_match_entry(
            confirm_template,
            region_ltrb=region_ltrb,
            threshold=confirm_threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
            screenshot=screenshot,
        )
        entry["key"] = confirm_key
        confirm_candidates.append(entry)
    passed_confirm_candidates = [entry for entry in confirm_candidates if entry.get("passed_threshold")]
    if passed_confirm_candidates:
        confirm_entry = max(passed_confirm_candidates, key=lambda entry: entry.get("score") or 0.0)
    elif confirm_candidates:
        confirm_entry = max(confirm_candidates, key=lambda entry: entry.get("score") or 0.0)
    t_confirm = _time() - t0

    rows = []
    for item_name, matches in resolved_matches.items():
        if not matches:
            continue
        match = min(matches, key=lambda current: current[1])
        row_center_y = int(_match_center_y(match))
        paired_checkbox = _pair_item_to_increment(match, checkbox_matches, y_tolerance=36)
        checkbox_target = _to_absolute_click_target(constants.GAME_WINDOW_REGION, paired_checkbox) if paired_checkbox else None
        paired_purchased = _pair_item_to_increment(match, purchased_matches, y_tolerance=45)
        rows.append(
            {
                "item_name": item_name,
                "category": constants.TRACKBLAZER_ITEM_CATEGORIES.get(item_name, "unknown"),
                "threshold": _item_threshold(item_name, threshold),
                "match": [int(v) for v in match],
                "row_center_y": row_center_y,
                "search_image_path": icon_search_image_path,
                "checkbox_match": [int(v) for v in paired_checkbox] if paired_checkbox else None,
                "checkbox_target": (
                    int(checkbox_target[0]),
                    int(checkbox_target[1]),
                ) if checkbox_target else None,
                "purchased": bool(paired_purchased),
                "detected": True,
            }
        )

    rows = sorted(rows, key=lambda entry: entry["row_center_y"])
    visible_items = [entry["item_name"] for entry in rows]
    purchasable_items = [entry["item_name"] for entry in rows if not entry.get("purchased")]

    purchased_items = [entry["item_name"] for entry in rows if entry.get("purchased")]
    timing = {
        "total": round(_time() - t_total, 4),
        "capture": round(t_capture, 4),
        "templates": round(t_templates, 4),
        "families": round(t_families, 4),
        "families_resolved": families_resolved,
        "families_skipped": families_skipped,
        "checkboxes": round(t_checkboxes, 4),
        "confirm": round(t_confirm, 4),
        "rows_detected": len(rows),
        "checkbox_count": len(checkbox_matches),
        "purchased_count": len(purchased_matches),
    }
    info(
        f"[TB_SHOP] visible rows={len(rows)} items={visible_items} "
        f"purchasable={purchasable_items}{' purchased=' + str(purchased_items) if purchased_items else ''} "
        f"checkboxes={len(checkbox_matches)} confirm={bool(confirm_entry and confirm_entry.get('passed_threshold'))}"
    )
    return {
        "visible_items": visible_items,
        "purchasable_items": purchasable_items,
        "rows": rows,
        "checkbox_matches": [[int(v) for v in match] for match in checkbox_matches],
        "confirm": confirm_entry,
        "scroll_ready": False,
        "todo": "add scrolling to scan more than the current visible page",
        "timing": timing,
    }


def scroll_trackblazer_shop(direction="down", duration=0.55, settle_seconds=1.0):
    """Scroll the Trackblazer shop list using the configured swipe region.

    direction="down" reveals lower items by swiping bottom->top.
    direction="up" reveals higher items by swiping top->bottom.
    """
    normalized = str(direction or "down").strip().lower()
    if normalized not in ("down", "up"):
        raise ValueError(f"Unsupported shop scroll direction: {direction}")

    if normalized == "down":
        start = constants.MANT_SHOP_SCROLL_BOTTOM_MOUSE_POS
        end = constants.MANT_SHOP_SCROLL_TOP_MOUSE_POS
    else:
        start = constants.MANT_SHOP_SCROLL_TOP_MOUSE_POS
        end = constants.MANT_SHOP_SCROLL_BOTTOM_MOUSE_POS

    swiped = device_action.swipe(
        start,
        end,
        duration=duration,
        text=f"Trackblazer shop scroll {normalized}",
    )
    if swiped:
        sleep(settle_seconds)
    return {
        "direction": normalized,
        "start": [int(start[0]), int(start[1])],
        "end": [int(end[0]), int(end[1])],
        "duration": float(duration),
        "settle_seconds": float(settle_seconds),
        "swiped": bool(swiped),
    }


def _append_shop_scan_page(flow, page, page_index, seen_items, ordered_items, capture_mode, scrollbar=None, elapsed=None, seen_purchasable=None, ordered_purchasable=None):
    visible_items = list(page.get("visible_items") or [])
    purchasable_items = list(page.get("purchasable_items") or [])
    signature = tuple(visible_items)
    new_items = [item_name for item_name in visible_items if item_name not in seen_items]
    for item_name in new_items:
        seen_items.add(item_name)
        ordered_items.append(item_name)
    if seen_purchasable is not None and ordered_purchasable is not None:
        for item_name in purchasable_items:
            if item_name not in seen_purchasable:
                seen_purchasable.add(item_name)
                ordered_purchasable.append(item_name)

    flow["pages"].append(
        {
            "page_index": int(page_index),
            "capture_mode": str(capture_mode),
            "elapsed": round(float(elapsed or 0.0), 4),
            "visible_items": visible_items,
            "purchasable_items": purchasable_items,
            "new_items": new_items,
            "rows": page.get("rows"),
            "confirm": page.get("confirm"),
            "timing": page.get("timing"),
            "scrollbar": scrollbar,
        }
    )
    return signature, new_items


def _scan_all_trackblazer_shop_items_paged(
    threshold=0.8,
    checkbox_threshold=0.8,
    confirm_threshold=0.8,
    max_reset_swipes=4,
    max_forward_swipes=12,
):
    """Scan the full Trackblazer shop by paging through visible rows.

    This first pass uses repeated visible-item scans and stops once further
    down-swipes stop producing new rows. The scrollbar region is available for
    future refinement, but item-diffing is the primary stop rule for now.
    """
    t_total = _time()
    flow = {
        "reset_swipes": [],
        "forward_swipes": [],
        "pages": [],
        "stop_reason": "",
        "scan_mode": "paged_swipe_fallback",
    }

    # Best-effort reset toward the top so the scan starts from a consistent state.
    for _ in range(max_reset_swipes):
        flow["reset_swipes"].append(scroll_trackblazer_shop(direction="up"))

    seen_items = set()
    ordered_items = []
    seen_purchasable = set()
    ordered_purchasable = []
    seen_signatures = set()
    stale_pages = 0
    last_page = None

    for page_index in range(max_forward_swipes + 1):
        page = scan_trackblazer_shop_inventory(
            threshold=threshold,
            checkbox_threshold=checkbox_threshold,
            confirm_threshold=confirm_threshold,
        )
        signature, new_items = _append_shop_scan_page(
            flow,
            page,
            page_index,
            seen_items,
            ordered_items,
            capture_mode="paged_swipe",
            seen_purchasable=seen_purchasable,
            ordered_purchasable=ordered_purchasable,
        )

        if signature in seen_signatures or (not new_items and last_page == signature):
            stale_pages += 1
        else:
            stale_pages = 0
        seen_signatures.add(signature)
        last_page = signature

        if stale_pages >= 1:
            flow["stop_reason"] = "no_new_visible_items_after_scroll"
            break

        if page_index >= max_forward_swipes:
            flow["stop_reason"] = "max_forward_swipes_reached"
            break

        flow["forward_swipes"].append(scroll_trackblazer_shop(direction="down"))

    flow["timing_total"] = round(_time() - t_total, 4)
    flow["timing"] = {
        "mode": flow.get("scan_mode"),
        "pages": len(flow.get("pages") or []),
        "reset_swipes": len(flow.get("reset_swipes") or []),
        "forward_swipes": len(flow.get("forward_swipes") or []),
        "total": round(flow["timing_total"], 4),
    }
    return {
        "all_items": ordered_items,
        "purchasable_items": ordered_purchasable,
        "pages": flow["pages"],
        "flow": flow,
    }


def _capture_shop_frames_during_scrollbar_drag(
    threshold,
    checkbox_threshold,
    confirm_threshold,
    initial_scrollbar,
):
    def _analyze_buffered_frame(frame_payload):
        frame_scan_t0 = _time()
        page = scan_trackblazer_shop_inventory(
            threshold=threshold,
            checkbox_threshold=checkbox_threshold,
            confirm_threshold=confirm_threshold,
            screenshot=frame_payload.get("screenshot"),
            save_debug_image=False,
        )
        scrollbar = inspect_trackblazer_shop_scrollbar(screenshot=frame_payload.get("screenshot"))
        scan_elapsed = _time() - frame_scan_t0
        return {
            "index": int(frame_payload.get("index", 0)),
            "elapsed": frame_payload.get("elapsed"),
            "page": page,
            "scrollbar": scrollbar,
            "final": bool(frame_payload.get("final")),
            "timing": {
                "capture": round(frame_payload.get("capture_elapsed", 0.0), 4),
                "scan": round(scan_elapsed, 4),
                "wall": round(frame_payload.get("capture_elapsed", 0.0) + scan_elapsed, 4),
            },
        }

    def _analysis_worker():
        while True:
            frame_payload = analysis_queue.get()
            if frame_payload is None:
                analysis_queue.task_done()
                return
            try:
                analyzed = _analyze_buffered_frame(frame_payload)
                analyzed_frames.append(analyzed)
            finally:
                analysis_queue.task_done()

    drag = {
        "start": None,
        "end": None,
        "duration": float(_SHOP_SCROLLBAR_DRAG_DURATION_SECONDS),
        "swiped": False,
        "frames": [],
        "stop_reason": "",
        "timing": {},
    }
    thumb_center = (initial_scrollbar or {}).get("thumb_center")
    bbox = (initial_scrollbar or {}).get("bbox") or [int(v) for v in constants.MANT_SHOP_SCROLLBAR_BBOX]
    if not thumb_center:
        drag["stop_reason"] = "scrollbar_thumb_not_detected"
        return drag

    drag_start = (int(thumb_center[0]), int(thumb_center[1]))
    drag_end = (int(initial_scrollbar.get("track_center_x") or thumb_center[0]), int(bbox[3] - _SHOP_SCROLLBAR_DRAG_END_PADDING))
    drag["start"] = [int(drag_start[0]), int(drag_start[1])]
    drag["end"] = [int(drag_end[0]), int(drag_end[1])]

    t_drag = _time()
    capture_total = 0.0
    frame_count = 0
    analyzed_frames = []
    analysis_queue = Queue()
    analysis_workers = []
    worker_count = max(1, _SHOP_SCROLLBAR_ANALYSIS_WORKERS)
    for _ in range(worker_count):
        worker = threading.Thread(target=_analysis_worker, daemon=True)
        worker.start()
        analysis_workers.append(worker)

    def _run_drag():
        drag["swiped"] = bool(device_action.swipe(
            drag_start,
            drag_end,
            duration=_SHOP_SCROLLBAR_DRAG_DURATION_SECONDS,
            text="Trackblazer shop scrollbar drag",
        ))

    drag_thread = threading.Thread(target=_run_drag, daemon=True)
    drag_thread.start()
    next_capture_at = _time() + _SHOP_SCROLLBAR_FRAME_INTERVAL_SECONDS
    while drag_thread.is_alive():
        now = _time()
        if now < next_capture_at:
            sleep(max(0.0, next_capture_at - now))
        frame_capture_t0 = _time()
        screenshot = _capture_live_trackblazer_ui_screenshot()
        capture_elapsed = _time() - frame_capture_t0
        capture_total += capture_elapsed
        analysis_queue.put({
            "index": frame_count,
            "elapsed": round(_time() - t_drag, 4),
            "capture_elapsed": capture_elapsed,
            "screenshot": screenshot,
        })
        frame_count += 1
        next_capture_at = max(next_capture_at + _SHOP_SCROLLBAR_FRAME_INTERVAL_SECONDS, _time() + 0.001)

    drag_thread.join()
    final_capture_t0 = _time()
    final_screenshot = _capture_live_trackblazer_ui_screenshot()
    final_capture_elapsed = _time() - final_capture_t0
    capture_total += final_capture_elapsed
    analysis_queue.put({
        "index": frame_count,
        "elapsed": round(_time() - t_drag, 4),
        "capture_elapsed": final_capture_elapsed,
        "screenshot": final_screenshot,
        "final": True,
    })
    frame_count += 1
    capture_window = _time() - t_drag

    scan_total = 0.0
    analysis_queue.join()
    for _ in analysis_workers:
        analysis_queue.put(None)
    for worker in analysis_workers:
        worker.join()
    drag["frames"] = sorted(analyzed_frames, key=lambda frame: int(frame.get("index", 0)))
    for analyzed_frame in drag["frames"]:
        scan_total += float(((analyzed_frame.get("timing") or {}).get("scan") or 0.0))

    final_scrollbar = (
        ((drag.get("frames") or [])[-1] or {}).get("scrollbar")
        if (drag.get("frames") or [])
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
        "frame_interval_target": round(_SHOP_SCROLLBAR_FRAME_INTERVAL_SECONDS, 4),
        "frames": int(frame_count),
        "capture_total": round(capture_total, 4),
        "scan_total": round(scan_total, 4),
        "analysis_total": round(max(0.0, wall_total - capture_window), 4),
        "wall": round(wall_total, 4),
    }
    return drag


def scan_all_trackblazer_shop_items(
    threshold=0.8,
    checkbox_threshold=0.8,
    confirm_threshold=0.8,
    max_reset_swipes=4,
    max_forward_swipes=12,
):
    """Scan the full Trackblazer shop using a scrollbar-thumb drag when available."""
    t_total = _time()
    flow = {
        "reset_swipes": [],
        "forward_swipes": [],
        "pages": [],
        "stop_reason": "",
        "scan_mode": "scrollbar_drag",
        "scrollbar_initial": None,
        "scrollbar_final": None,
        "continuous_drag": None,
    }

    pre_reset_screenshot = _capture_live_trackblazer_ui_screenshot()
    pre_reset_scrollbar = inspect_trackblazer_shop_scrollbar(screenshot=pre_reset_screenshot)
    if pre_reset_scrollbar.get("detected") and pre_reset_scrollbar.get("scrollable"):
        if not pre_reset_scrollbar.get("is_at_top"):
            flow["reset_swipes"].append(_drag_trackblazer_shop_scrollbar(pre_reset_scrollbar, edge="top"))
    else:
        for _ in range(max_reset_swipes):
            flow["reset_swipes"].append(scroll_trackblazer_shop(direction="up"))

    seen_items = set()
    ordered_items = []
    seen_purchasable = set()
    ordered_purchasable = []
    seen_signatures = set()
    stale_pages = 0
    last_page = None

    initial_capture_t0 = _time()
    initial_screenshot = _capture_live_trackblazer_ui_screenshot()
    initial_capture_elapsed = _time() - initial_capture_t0
    initial_scrollbar = inspect_trackblazer_shop_scrollbar(screenshot=initial_screenshot)
    flow["scrollbar_initial"] = initial_scrollbar

    initial_page = scan_trackblazer_shop_inventory(
        threshold=threshold,
        checkbox_threshold=checkbox_threshold,
        confirm_threshold=confirm_threshold,
        screenshot=initial_screenshot,
    )
    initial_page_timing = dict(initial_page.get("timing") or {})
    initial_page_timing["capture"] = round(initial_page_timing.get("capture", 0.0) + initial_capture_elapsed, 4)
    initial_page_timing["wall"] = round(initial_page_timing.get("total", 0.0) + initial_capture_elapsed, 4)
    initial_page["timing"] = initial_page_timing
    initial_signature, _ = _append_shop_scan_page(
        flow,
        initial_page,
        0,
        seen_items,
        ordered_items,
        capture_mode="initial",
        scrollbar=initial_scrollbar,
        elapsed=0.0,
        seen_purchasable=seen_purchasable,
        ordered_purchasable=ordered_purchasable,
    )
    seen_signatures.add(initial_signature)
    last_page = initial_signature

    if not initial_scrollbar.get("detected") or not initial_scrollbar.get("scrollable"):
        flow["stop_reason"] = "no_scrollbar_detected_or_not_scrollable"
        flow["scrollbar_final"] = initial_scrollbar
    else:
        drag_result = _capture_shop_frames_during_scrollbar_drag(
            threshold=threshold,
            checkbox_threshold=checkbox_threshold,
            confirm_threshold=confirm_threshold,
            initial_scrollbar=initial_scrollbar,
        )
        flow["continuous_drag"] = drag_result
        frame_offset = len(flow["pages"])
        for frame_index, frame in enumerate(drag_result.get("frames") or [], start=frame_offset):
            page = dict(frame.get("page") or {})
            page_timing = dict(page.get("timing") or {})
            frame_timing = frame.get("timing") or {}
            page_timing["capture"] = round(frame_timing.get("capture", page_timing.get("capture", 0.0)), 4)
            page_timing["wall"] = round(frame_timing.get("wall", page_timing.get("total", 0.0)), 4)
            page["timing"] = page_timing
            signature, new_items = _append_shop_scan_page(
                flow,
                page,
                frame_index,
                seen_items,
                ordered_items,
                capture_mode="scrollbar_drag_frame",
                scrollbar=frame.get("scrollbar"),
                elapsed=frame.get("elapsed"),
                seen_purchasable=seen_purchasable,
                ordered_purchasable=ordered_purchasable,
            )
            if signature in seen_signatures or (not new_items and last_page == signature):
                stale_pages += 1
            else:
                stale_pages = 0
            seen_signatures.add(signature)
            last_page = signature
        flow["scrollbar_final"] = (
            ((drag_result.get("frames") or [])[-1] or {}).get("scrollbar")
            if (drag_result.get("frames") or [])
            else initial_scrollbar
        )

        if drag_result.get("stop_reason") == "scrollbar_drag_failed":
            fallback_result = _scan_all_trackblazer_shop_items_paged(
                threshold=threshold,
                checkbox_threshold=checkbox_threshold,
                confirm_threshold=confirm_threshold,
                max_reset_swipes=0,
                max_forward_swipes=max_forward_swipes,
            )
            fallback_flow = fallback_result.get("flow") or {}
            flow["scan_mode"] = "paged_swipe_fallback"
            flow["forward_swipes"] = list(fallback_flow.get("forward_swipes") or [])
            flow["pages"] = list(flow["pages"]) + list(fallback_flow.get("pages") or [])
            flow["stop_reason"] = fallback_flow.get("stop_reason") or "paged_fallback_after_drag_failure"
            flow["scrollbar_final"] = flow.get("scrollbar_final") or initial_scrollbar
            ordered_items = list(dict.fromkeys(list(ordered_items) + list(fallback_result.get("all_items") or [])))
            ordered_purchasable = list(dict.fromkeys(list(ordered_purchasable) + list(fallback_result.get("purchasable_items") or [])))
        elif flow["scrollbar_final"] and flow["scrollbar_final"].get("is_at_bottom"):
            flow["stop_reason"] = "scrollbar_bottom_reached"
        elif stale_pages >= 2:
            flow["stop_reason"] = "no_new_visible_items_during_drag"
        else:
            flow["stop_reason"] = drag_result.get("stop_reason") or "drag_completed"

    flow["timing_total"] = round(_time() - t_total, 4)
    flow["timing"] = {
        "mode": flow.get("scan_mode"),
        "pages": len(flow.get("pages") or []),
        "reset_swipes": len(flow.get("reset_swipes") or []),
        "drag": ((flow.get("continuous_drag") or {}).get("timing") or {}),
        "initial_scrollbar_detected": bool((flow.get("scrollbar_initial") or {}).get("detected")),
        "initial_scrollbar_scrollable": bool((flow.get("scrollbar_initial") or {}).get("scrollable")),
        "stale_pages": int(stale_pages),
        "total": round(flow["timing_total"], 4),
    }
    return {
        "all_items": ordered_items,
        "purchasable_items": ordered_purchasable,
        "pages": flow["pages"],
        "flow": flow,
    }


def _filter_shop_checkbox_matches(matches):
    return [
        (int(x), int(y), int(w), int(h))
        for (x, y, w, h) in (matches or [])
        if int(x) >= 560 and int(y) >= 450 and int(y) <= 1100
    ]


def _resolve_shop_row_checkbox_state(screenshot, row_match, threshold=0.8):
    """Resolve whether a shop row is selected and where its checkbox lives."""
    state = {
        "state": "unknown",
        "checked_match": None,
        "unchecked_match": None,
        "click_target": None,
    }
    if screenshot is None or row_match is None:
        return state
    checked_template = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("select_checked")
    unchecked_template = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("select_unchecked")
    checked_matches = _filter_shop_checkbox_matches(
        device_action.match_template(
            checked_template,
            screenshot,
            threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        ) if checked_template else []
    )
    unchecked_matches = _filter_shop_checkbox_matches(
        device_action.match_template(
            unchecked_template,
            screenshot,
            threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        ) if unchecked_template else []
    )
    paired_checked = _pair_item_to_increment(row_match, checked_matches, y_tolerance=36)
    paired_unchecked = _pair_item_to_increment(row_match, unchecked_matches, y_tolerance=36)
    if paired_checked:
        state["state"] = "selected"
        state["checked_match"] = [int(v) for v in paired_checked]
    if paired_unchecked:
        state["unchecked_match"] = [int(v) for v in paired_unchecked]
        state["click_target"] = list(_to_absolute_click_target(constants.GAME_WINDOW_REGION, paired_unchecked))
        if state["state"] != "selected":
            state["state"] = "unselected"
    return state


def _match_single_shop_item(screenshot, item_name, threshold=0.7):
    """Match a single item template against a shop screenshot.

    Returns a row dict compatible with scan_trackblazer_shop_inventory rows,
    or None if the item is not found.  For family-variant items (megaphones,
    ankle weights, cleat hammers, etc.) delegates to
    ``_resolve_shop_family_rows`` so that colour/spark disambiguation is
    applied and siblings visible on the same page don't confuse the result.
    """
    template_path = constants.TRACKBLAZER_ITEM_TEMPLATES.get(item_name)
    if not template_path or screenshot is None:
        return None
    family_items = _ITEM_VARIANT_FAMILY_MAP.get(item_name)
    if family_items and len(family_items) > 1:
        family_rows = _resolve_shop_family_rows(screenshot, family_items, threshold)
        for row in family_rows:
            if row.get("item_name") == item_name:
                return row
        return None
    icon_screenshot, icon_offset_y = _shop_icon_search_crop(screenshot)
    if icon_screenshot is None:
        return None
    item_threshold = _item_threshold(item_name, threshold)
    matches = device_action.match_template(
        template_path,
        icon_screenshot,
        item_threshold,
        template_scaling=_INVERSE_GLOBAL_SCALE,
    )
    if not matches:
        return None
    best = min(matches, key=lambda m: m[1])
    match = (int(best[0]), int(best[1] + icon_offset_y), int(best[2]), int(best[3]))
    return {
        "item_name": item_name,
        "match": list(match),
        "row_center_y": int(_match_center_y(match)),
    }


def prepare_trackblazer_shop_item_selection(
    item_name,
    quantity=1,
    threshold=0.7,
    checkbox_threshold=0.8,
    confirm_threshold=0.7,
    scan_result=None,
):
    """Find a shop item, select its row checkbox once, and stop before confirm."""
    requested_item = str(item_name or "").strip()
    t_total = _time()
    flow = {
        "requested_item": requested_item,
        "requested_quantity": int(max(1, quantity or 1)),
        "selection_only": True,
        "confirm_pressed": False,
        "scan_result": None,
        "scan_timing": {},
        "target_page": None,
        "seek_result": None,
        "attempts": [],
        "selected": False,
        "already_selected": False,
        "reason": "",
    }
    if not requested_item:
        flow["reason"] = "missing_item_name"
        flow["timing_total"] = round(_time() - t_total, 4)
        return flow

    if scan_result is None:
        scan_result = scan_all_trackblazer_shop_items(
            threshold=threshold,
            checkbox_threshold=checkbox_threshold,
            confirm_threshold=confirm_threshold,
        )
    flow["scan_result"] = scan_result
    flow["scan_timing"] = (scan_result.get("flow") or {}).get("timing") or {}

    target_candidates = []
    for page in (scan_result.get("pages") or []):
        for row in (page.get("rows") or []):
            if row.get("item_name") != requested_item:
                continue
            target_candidates.append({
                "page_index": page.get("page_index"),
                "capture_mode": page.get("capture_mode"),
                "scrollbar": page.get("scrollbar") or {},
                "row": row,
            })
    if not target_candidates:
        flow["reason"] = "item_not_found_in_shop_scan"
        flow["timing_total"] = round(_time() - t_total, 4)
        return flow

    # Collect ALL unique scroll ratios where the item was observed during
    # the initial scan.  The continuous-drag scan captures frames while the
    # scrollbar is in motion so any single ratio may not exactly reproduce
    # the original view.  Using every observed ratio (with offsets) gives the
    # selection loop the best chance of re-finding the item.
    observed_ratios = sorted(set(
        float((entry.get("scrollbar") or {}).get("position_ratio"))
        for entry in target_candidates
        if (entry.get("scrollbar") or {}).get("position_ratio") is not None
    ))
    # Pick the median as the primary reference for flow logging.
    target_page = min(
        target_candidates,
        key=lambda entry: abs(
            float((entry.get("scrollbar") or {}).get("position_ratio") or 0.0)
            - (observed_ratios[len(observed_ratios) // 2] if observed_ratios else 0.0)
        ),
    )
    flow["target_page"] = {
        "page_index": target_page.get("page_index"),
        "capture_mode": target_page.get("capture_mode"),
        "scrollbar_ratio": (target_page.get("scrollbar") or {}).get("position_ratio"),
        "observed_ratios": list(observed_ratios),
        "row": target_page.get("row"),
    }

    ratio_candidates = []
    if observed_ratios:
        # Start with each observed ratio, then fan out with offsets.
        for obs_ratio in observed_ratios:
            ratio_candidates.append(obs_ratio)
        for obs_ratio in observed_ratios:
            for delta in (-0.04, 0.04, -0.08, 0.08, -0.12, 0.12):
                ratio_candidates.append(min(1.0, max(0.0, obs_ratio + delta)))
    else:
        ratio_candidates.append(0.0)

    seen_ratios = set()
    ratio_candidates = [
        ratio for ratio in ratio_candidates
        if not (round(float(ratio), 4) in seen_ratios or seen_ratios.add(round(float(ratio), 4)))
    ]

    for ratio in ratio_candidates:
        current_scrollbar = inspect_trackblazer_shop_scrollbar()
        seek_result = _drag_trackblazer_shop_scrollbar_to_ratio(current_scrollbar, ratio)
        flow["seek_result"] = seek_result
        live_screenshot = _capture_live_trackblazer_ui_screenshot()
        live_scrollbar = inspect_trackblazer_shop_scrollbar(screenshot=live_screenshot)
        # Direct single-template match — bypasses family variant resolution
        # so that sibling items (e.g. artisan/master cleat hammer) visible on
        # the same page don't suppress the item we're looking for.
        matched_row = _match_single_shop_item(live_screenshot, requested_item, threshold=threshold)
        attempt = {
            "target_ratio": round(float(ratio), 4),
            "scrollbar": live_scrollbar,
            "row_found": bool(matched_row),
            "row": matched_row,
            "checkbox_state": None,
            "click_result": None,
        }
        if not matched_row:
            flow["attempts"].append(attempt)
            continue

        checkbox_state = _resolve_shop_row_checkbox_state(live_screenshot, matched_row.get("match"), threshold=checkbox_threshold)
        attempt["checkbox_state"] = checkbox_state
        if checkbox_state.get("state") == "selected":
            flow["already_selected"] = True
            flow["selected"] = True
            flow["attempts"].append(attempt)
            break
        click_target = checkbox_state.get("click_target") or matched_row.get("checkbox_target")
        if not click_target:
            flow["attempts"].append(attempt)
            continue

        click_result = device_action.click_with_metrics(
            click_target,
            text=f"[TB_SHOP] Select '{requested_item}' once without confirm.",
        )
        clicked = bool(click_result.get("clicked"))
        bot.push_debug_history({
            "event": "click",
            "asset": f"shop_increment_{requested_item}",
            "result": "clicked" if clicked else "click_failed",
            "context": "trackblazer_shop_purchase",
        })
        attempt["click_result"] = click_result
        verify_screenshot = _capture_live_trackblazer_ui_screenshot()
        # Direct match for verification too — same family resolution bypass.
        verify_row = _match_single_shop_item(verify_screenshot, requested_item, threshold=threshold)
        verify_state = _resolve_shop_row_checkbox_state(
            verify_screenshot,
            verify_row.get("match") if verify_row else None,
            threshold=checkbox_threshold,
        )
        attempt["verify_checkbox_state"] = verify_state
        flow["attempts"].append(attempt)
        flow["selected"] = clicked
        if verify_state.get("state") == "selected":
            flow["selected"] = True
        if flow["selected"]:
            break

    if not flow["selected"] and not flow["reason"]:
        flow["reason"] = "failed_to_select_item_checkbox"
    elif flow["selected"] and not flow["already_selected"]:
        flow["reason"] = "item_checkbox_selected"
    elif flow["already_selected"]:
        flow["reason"] = "item_already_selected"
    flow["timing_total"] = round(_time() - t_total, 4)
    return flow


def execute_trackblazer_shop_purchases(item_keys, trigger="automatic"):
    """Buy the requested Trackblazer shop items once each, then close the shop."""
    requested_keys = [str(item_key) for item_key in (item_keys or []) if item_key]
    clear_runtime_ocr_debug()

    catalog = {entry["key"]: entry for entry in get_shop_catalog()}
    flow = {
        "trigger": str(trigger or "automatic"),
        "execution_intent": bot.get_execution_intent(),
        "requested_items": list(requested_keys),
        "entered": False,
        "closed": False,
        "success": False,
        "reason": "",
        "entry_result": None,
        "selection_attempts": [],
        "missing_items": [],
        "control_detection_result": None,
        "confirm_click_target": None,
        "confirm_clicked": False,
        "confirm_click_result": None,
        "post_confirm_controls": None,
        "dismiss_after_sale_clicked": False,
        "dismiss_after_sale_result": None,
        "close_result": None,
    }
    result = {
        "trackblazer_shop_items": list(requested_keys),
        "trackblazer_shop_summary": {
            "items_detected": list(requested_keys),
            "page_count": 0,
            "stop_reason": "",
            "shop_coins": -1,
        },
        "trackblazer_shop_flow": flow,
        "ocr_runtime_debug": {},
        "inventory_ocr_debug_entries": [],
        "success": False,
    }

    if not requested_keys:
        flow["closed"] = True
        flow["success"] = True
        flow["reason"] = "no_requested_items"
        result["success"] = True
        return result

    t_total = _time()

    try:
        t0 = _time()
        entry_result = enter_shop(threshold=0.8, read_shop_coins=False)
        flow["timing_open"] = round(_time() - t0, 3)
        flow["entry_result"] = entry_result
        flow["entered"] = bool(entry_result.get("entered"))
        result["trackblazer_shop_summary"]["shop_coins"] = entry_result.get("shop_coins", -1)
        if not flow["entered"]:
            flow["reason"] = entry_result.get("reason") or "failed_to_enter_shop"
            if entry_result.get("clicked"):
                warning(
                    "[TB_SHOP] Shop entry button was clicked but verification failed; "
                    "attempting recovery close in case shop is actually open."
                )
                t0 = _time()
                close_result = close_trackblazer_shop()
                flow["timing_recovery_close"] = round(_time() - t0, 3)
                flow["recovery_close_result"] = close_result
                flow["closed"] = bool(close_result.get("closed"))
            return result

        select_t0 = _time()
        shared_scan = scan_all_trackblazer_shop_items(
            threshold=0.7,
            checkbox_threshold=0.8,
            confirm_threshold=0.7,
        )
        flow["scan_timing"] = (shared_scan.get("flow") or {}).get("timing") or {}

        # Reset scrollbar to the top before selecting items — the scan
        # leaves the scrollbar at the bottom and subsequent per-item
        # seeks are unreliable when starting from the bottom edge.
        post_scan_scrollbar = inspect_trackblazer_shop_scrollbar()
        if post_scan_scrollbar.get("detected") and not post_scan_scrollbar.get("is_at_top"):
            reset_result = _drag_trackblazer_shop_scrollbar(post_scan_scrollbar, edge="top")
            flow["pre_select_reset"] = reset_result
            sleep(0.3)
        else:
            # Fallback: swipe up if scrollbar detection failed after scan
            for _ in range(3):
                scroll_trackblazer_shop(direction="up")
            flow["pre_select_reset"] = {"direction": "swipe_up_fallback", "swiped": True}
            sleep(0.3)

        # Sort items by their scan scroll position (top-to-bottom) so seeks
        # are short monotonic drags rather than back-and-forth jumps.
        def _item_scroll_ratio(key):
            for page in (shared_scan.get("pages") or []):
                for row in (page.get("rows") or []):
                    if row.get("item_name") == key:
                        return (page.get("scrollbar") or {}).get("position_ratio") or 0.0
            return 999.0
        sorted_keys = sorted(requested_keys, key=_item_scroll_ratio)

        for item_key in sorted_keys:
            catalog_entry = catalog.get(item_key) or {}
            item_name = catalog_entry.get("display_name") or str(item_key).replace("_", " ").title()
            selection_result = prepare_trackblazer_shop_item_selection(item_key, scan_result=shared_scan)
            attempt = {
                "item_key": item_key,
                "item_name": item_name,
                "selection_result": selection_result,
                "selected": bool(selection_result.get("selected")),
            }
            flow["selection_attempts"].append(attempt)
            if not attempt["selected"]:
                flow["missing_items"].append(item_key)
        flow["timing_select_items"] = round(_time() - select_t0, 3)

        if flow["missing_items"]:
            flow["reason"] = "shop_items_not_selectable"
            return result

        sleep(0.3)
        controls_t0 = _time()
        controls = detect_inventory_controls()
        flow["timing_controls"] = round(_time() - controls_t0, 3)
        # Prefer the best passing confirm candidate resolved by
        # detect_inventory_controls (which checks all shop_confirm variants).
        # Fall back to the specific "shop_confirm" candidate only if the
        # general best is missing.
        confirm_entry = controls.get("confirm_use") or (controls.get("confirm_candidates") or {}).get("shop_confirm") or {}
        flow["control_detection_result"] = {
            "confirm_key": confirm_entry.get("key"),
            "confirm_visible": bool(confirm_entry.get("passed_threshold")),
            "confirm_click_target": list(confirm_entry.get("click_target")) if confirm_entry.get("click_target") else None,
            "confirm_score": confirm_entry.get("score"),
        }
        flow["confirm_click_target"] = flow["control_detection_result"]["confirm_click_target"]
        if not confirm_entry.get("passed_threshold") or not confirm_entry.get("click_target"):
            flow["reason"] = "shop_confirm_not_available"
            return result

        confirm_t0 = _time()
        confirm_click_result = device_action.click_with_metrics(
            confirm_entry.get("click_target"),
            text="[TB_SHOP] Confirm planned shop purchase.",
        )
        flow["timing_confirm"] = round(_time() - confirm_t0, 3)
        flow["confirm_click_result"] = confirm_click_result
        flow["confirm_clicked"] = bool(confirm_click_result.get("clicked"))
        if not flow["confirm_clicked"]:
            flow["reason"] = "failed_to_click_shop_confirm"
            return result

        sleep(0.25)
        post_confirm_controls_t0 = _time()
        post_confirm_controls = detect_inventory_controls()
        flow["timing_post_confirm_controls"] = round(_time() - post_confirm_controls_t0, 3)
        flow["post_confirm_controls"] = post_confirm_controls

        dismiss_t0 = _time()
        post_confirm_close = _wait_for_post_shop_close_button()
        flow["timing_dismiss_after_sale"] = round(_time() - dismiss_t0, 3)
        flow["dismiss_after_sale_result"] = post_confirm_close
        flow["dismiss_after_sale_clicked"] = bool(post_confirm_close.get("clicked"))
        flow["dismiss_after_sale_closed"] = bool(post_confirm_close.get("closed"))

        verify_open_t0 = _time()
        shop_open, _, verify_checks = detect_shop_screen(threshold=0.75)
        flow["timing_verify_shop_open"] = round(_time() - verify_open_t0, 3)
        flow["verification_checks"] = verify_checks
        if flow["dismiss_after_sale_closed"]:
            flow["closed"] = True
        else:
            # Always make one explicit close attempt after purchase if the
            # exchange-complete dialog did not verify closed. The caller must
            # not proceed into the main action with the shop overlay still up.
            close_t0 = _time()
            close_result = close_trackblazer_shop()
            flow["timing_close"] = round(_time() - close_t0, 3)
            flow["close_result"] = close_result
            flow["closed"] = bool(close_result.get("closed"))
            if not flow["closed"] and not shop_open:
                flow["closed"] = True

        if not flow["closed"] and not flow["reason"]:
            flow["reason"] = "failed_to_close_shop"

        flow["success"] = bool(
            flow["entered"]
            and not flow["missing_items"]
            and flow["confirm_clicked"]
            and flow["closed"]
        )
        result["success"] = bool(flow["success"])
    finally:
        # Always try to close the shop if we entered but haven't closed yet.
        if flow.get("entered") and not flow.get("closed"):
            try:
                shop_open, _, _ = detect_shop_screen(threshold=0.75)
                if shop_open:
                    emergency_close = close_trackblazer_shop()
                    flow["emergency_close"] = True
                    flow["closed"] = bool(emergency_close.get("closed"))
                    if not flow.get("reason"):
                        flow["reason"] = "closed_after_error"
                else:
                    flow["closed"] = True
            except Exception as exc:
                warning(f"[TB_SHOP] Emergency shop close failed: {exc}")

        flow["timing_total"] = round(_time() - t_total, 3)
        result["trackblazer_shop_flow"] = flow
        result["success"] = bool(flow.get("success"))
        record_runtime_ocr_debug(
            "trackblazer_shop_execute_purchase",
            extra={
                "requested_items": list(requested_keys),
                "flow": flow,
            },
        )
        result["ocr_runtime_debug"] = snapshot_runtime_ocr_debug()
        result["inventory_ocr_debug_entries"] = _build_trackblazer_shop_debug_entries(
            flow,
            result["ocr_runtime_debug"],
        )

    return result


def execute_training_items(item_names, trigger="automatic", commit_mode="full"):
    """Unified Trackblazer training-item flow for all modes.

    Opens the inventory, scans items, verifies that the requested items are
    detected and have actionable increment targets, detects inventory controls,
    and then behaves according to ``commit_mode``:

    - ``"dry_run"``: No destructive clicks at all. Does NOT click increment
      controls or confirm-use. Records each item as simulated. Closes the
      inventory explicitly. Use for check_only / non-destructive scans.
    - ``"confirm_only"``: Clicks increment controls and the first confirm-use
      button but does NOT handle the follow-up confirmation prompt. Leaves
      the screen as-is for caller inspection.
    - ``"full"``: Production execute path. Clicks increments, confirm-use,
      handles the follow-up confirmation prompt, verifies the inventory
      auto-closes, and falls back to an explicit close if needed.
    """
    requested_items = [str(item_name) for item_name in (item_names or [])]
    clear_runtime_ocr_debug()

    flow = {
        "trigger": str(trigger or "automatic"),
        "execution_intent": bot.get_execution_intent(),
        "commit_mode": str(commit_mode),
        "requested_items": list(requested_items),
        "opened": False,
        "already_open": False,
        "closed": False,
        "skipped": False,
        "success": False,
        "reason": "",
        "open_result": None,
        "close_result": None,
        "increment_attempts": [],
        "missing_items": [],
        "missing_increment_targets": [],
        "confirm_use_available": False,
        "confirm_use_state": None,
        "confirm_use_click_target": None,
        "confirm_use_clicked": False,
        "confirm_use_click_result": None,
        "post_confirm_controls": None,
        "control_detection_result": None,
        "followup_confirm_visible": False,
        "followup_confirm_entry": None,
        "followup_confirm_clicked": False,
        "followup_confirm_click_result": None,
        "verification_checks": [],
        "graceful_noop": False,
    }
    result = {
        "requested_items": list(requested_items),
        "commit_mode": str(commit_mode),
        "trackblazer_inventory": CleanDefaultDict(),
        "trackblazer_inventory_summary": {
            "items_detected": [],
            "by_category": {},
            "total_detected": 0,
            "actionable_items": [],
        },
        "trackblazer_inventory_controls": {},
        "trackblazer_inventory_flow": flow,
        "inventory_ocr_debug_entries": [],
        "ocr_runtime_debug": {},
        "success": False,
    }

    commit_clicks = commit_mode in ("confirm_only", "full")
    handle_followup = commit_mode == "full"
    log_tag = "[TB_INV]"

    t_total = _time()
    inventory = CleanDefaultDict()
    controls = {}

    inventory_screen_open, _, precheck_entries = detect_inventory_screen()
    flow["precheck"] = precheck_entries

    if inventory_screen_open:
        flow["opened"] = True
        flow["already_open"] = True
    else:
        info(f"{log_tag} Opening Training Items inventory (commit_mode={commit_mode}).")
        t0 = _time()
        open_result = open_training_items_inventory(skip_precheck=True)
        flow["timing_open"] = round(_time() - t0, 3)
        flow["open_result"] = open_result
        flow["opened"] = bool(open_result.get("opened"))
        flow["already_open"] = bool(open_result.get("already_open"))
        if not flow["opened"]:
            flow["reason"] = "failed_to_open_inventory"
            warning(f"{log_tag} Failed to open Training Items inventory.")

    try:
        if flow["opened"]:
            # -- Scan --
            t0 = _time()
            inventory = scan_training_items_inventory()
            flow["timing_scan"] = round(_time() - t0, 3)
            flow["scan_timing"] = inventory.pop("_timing", None)
            result["trackblazer_inventory"] = inventory
            result["trackblazer_inventory_summary"] = build_inventory_summary(inventory)

            # -- Check requested items --
            for item_name in requested_items:
                item_data = inventory.get(item_name) or {}
                if not item_data.get("detected"):
                    flow["missing_items"].append(item_name)
                elif not item_data.get("increment_target"):
                    # Item detected but increment target missing or stale
                    # (on a different scroll page).  Not a hard failure yet
                    # — the increment loop below will scroll to find it.
                    if not item_data.get("increment_target_stale"):
                        flow["missing_increment_targets"].append(item_name)

            if flow["missing_items"]:
                missing_parts = [f"missing_items={flow['missing_items']}"]
                if flow["missing_increment_targets"]:
                    missing_parts.append(f"missing_increment_targets={flow['missing_increment_targets']}")
                flow["reason"] = "required_items_not_actionable"
                warning(f"{log_tag} Cannot continue item flow: {' '.join(missing_parts)}")
            elif flow["missing_increment_targets"]:
                flow["reason"] = "required_items_not_actionable"
                warning(
                    f"{log_tag} Cannot continue item flow: "
                    f"missing_increment_targets={flow['missing_increment_targets']}"
                )
            else:
                # -- Increment each requested item once --
                # In dry_run mode, record each item as planned but do NOT
                # click the increment controls — clicking them stages item
                # use, which is a destructive action.
                #
                # When the inventory is scrollable, items on a non-visible
                # page have ``increment_target_stale=True``.  Before
                # clicking, scroll to that page and re-scan to obtain a
                # fresh increment target.
                increment_t0 = _time()
                current_scroll = None  # track last scroll edge we moved to
                for item_name in requested_items:
                    item_data = inventory.get(item_name) or {}
                    target = item_data.get("increment_target")

                    # Determine if the item's targets are effectively stale.
                    # The initial scan sets increment_target_stale for items
                    # on the non-visible page, but scrolling during this loop
                    # can make previously-fresh targets stale too.
                    effectively_stale = item_data.get("increment_target_stale")
                    if not effectively_stale and current_scroll is not None:
                        page = item_data.get("scroll_page")
                        if page not in ("both", None) and current_scroll != page:
                            effectively_stale = True
                            info(
                                f"{log_tag} '{item_name}' targets stale: "
                                f"scroll_page={page} but current_scroll={current_scroll}"
                            )

                    if effectively_stale or (not target and item_data.get("detected")):
                        desired_edge = "top" if item_data.get("scroll_page") == "top" else "bottom"
                        if current_scroll != desired_edge:
                            info(f"{log_tag} Scrolling inventory to {desired_edge} for '{item_name}'.")
                            sb = inspect_trackblazer_inventory_scrollbar()
                            if sb.get("detected") and sb.get("scrollable"):
                                _drag_trackblazer_inventory_scrollbar(sb, edge=desired_edge)
                                sleep(_INV_SCROLLBAR_SETTLE_SECONDS)
                                device_action.flush_screenshot_cache()
                                current_scroll = desired_edge
                        # Re-scan single page for fresh increment targets.
                        # Update ALL requested items visible on this page so
                        # later iterations don't use stale coordinates.
                        rescan, _ = _scan_inventory_page()
                        for ri_name in requested_items:
                            ri_data = rescan.get(ri_name)
                            if ri_data and ri_data.get("increment_target"):
                                inventory[ri_name] = ri_data
                        rescan_data = rescan.get(item_name) or {}
                        if rescan_data.get("increment_target"):
                            target = rescan_data["increment_target"]
                            item_data = rescan_data
                            info(f"{log_tag} Re-scanned '{item_name}' after scroll: target={target}")
                        else:
                            warning(f"{log_tag} Re-scan after scroll did not find increment for '{item_name}'.")

                    attempt = {
                        "item_name": item_name,
                        "detected": bool(item_data.get("detected")),
                        "increment_target": list(target) if target else None,
                        "increment_match": item_data.get("increment_match"),
                        "row_center_y": item_data.get("row_center_y"),
                        "held_quantity": item_data.get("held_quantity"),
                        "scroll_page": item_data.get("scroll_page"),
                        "scrolled_to_find": bool(item_data.get("increment_target_stale")),
                        "click_metrics": None,
                        "clicked": False,
                        "simulated": commit_mode == "dry_run",
                    }
                    if not target:
                        warning(f"{log_tag} No increment target for '{item_name}', skipping click.")
                        bot.push_debug_history({
                            "event": "click",
                            "asset": f"inventory_increment_{item_name}",
                            "result": "target_missing",
                            "context": "trackblazer_item_use",
                        })
                        flow["missing_increment_targets"].append(item_name)
                        flow["increment_attempts"].append(attempt)
                        continue
                    if commit_mode == "dry_run":
                        info(
                            f"{log_tag} Would increment '{item_name}' at {attempt['increment_target']} "
                            f"(simulated, commit_mode={commit_mode})."
                        )
                        bot.push_debug_history({
                            "event": "click",
                            "asset": f"inventory_increment_{item_name}",
                            "result": "simulated",
                            "context": "trackblazer_item_use",
                        })
                    else:
                        info(f"{log_tag} Incrementing '{item_name}' once at {attempt['increment_target']}.")
                        click_metrics = device_action.click_with_metrics(
                            target,
                            text=f"{log_tag} Increment '{item_name}' once.",
                        )
                        attempt["click_metrics"] = click_metrics
                        attempt["clicked"] = bool(click_metrics.get("clicked"))
                        bot.push_debug_history({
                            "event": "click",
                            "asset": f"inventory_increment_{item_name}",
                            "result": "clicked" if attempt["clicked"] else "click_failed",
                            "context": "trackblazer_item_use",
                        })
                    flow["increment_attempts"].append(attempt)
                flow["timing_increments"] = round(_time() - increment_t0, 3)

            # -- Detect controls (all modes) --
            # Flush cache after increment clicks so we see the updated
            # confirm-use button state (available/unavailable).
            device_action.flush_screenshot_cache()
            controls_t0 = _time()
            controls = detect_inventory_controls()
            flow["timing_controls"] = round(_time() - controls_t0, 3)
            result["trackblazer_inventory_controls"] = controls

            confirm_use = controls.get("confirm_use") or {}
            flow["confirm_use_state"] = confirm_use.get("button_state")
            flow["confirm_use_available"] = confirm_use.get("button_state") == "available"
            flow["confirm_use_click_target"] = list(confirm_use.get("click_target")) if confirm_use.get("click_target") else None
            flow["control_detection_result"] = {
                "close_visible": bool((controls.get("close") or {}).get("passed_threshold")),
                "confirm_use_visible": bool(confirm_use.get("passed_threshold")),
                "confirm_use_key": confirm_use.get("key"),
                "confirm_use_button_state": confirm_use.get("button_state"),
                "confirm_use_button_state_reason": confirm_use.get("button_state_reason"),
                "confirm_use_score": confirm_use.get("score"),
                "confirm_use_click_target": flow["confirm_use_click_target"],
            }

            if flow["confirm_use_available"]:
                info(f"{log_tag} Confirm-use resolved to available/enabled after increments.")
            else:
                if commit_mode == "dry_run":
                    info(
                        f"{log_tag} Confirm-use remains unavailable in dry_run mode, as expected "
                        "because increment clicks were skipped."
                    )
                else:
                    if not flow["reason"]:
                        flow["reason"] = "confirm_use_not_available"
                    warning(f"{log_tag} Confirm-use did not resolve to available after increments.")
                    close_t0 = _time()
                    close_result = close_training_items_inventory()
                    flow["timing_close"] = round(_time() - close_t0, 3)
                    flow["close_result"] = close_result
                    flow["closed"] = bool(close_result.get("closed"))
                    flow["graceful_noop"] = bool(flow["closed"])
                    if flow["graceful_noop"]:
                        flow["reason"] = "confirm_use_not_available_closed_inventory"
                        info(
                            f"{log_tag} Confirm-use unavailable after increments; "
                            "closed inventory and continuing without item use."
                        )
                    elif not flow["reason"]:
                        flow["reason"] = "failed_to_close_inventory"

            # -- Commit phase: mode-dependent --
            if commit_clicks and flow["confirm_use_available"]:
                # confirm_only and full: click confirm-use
                info(f"{log_tag} Clicking confirm-use (commit_mode={commit_mode}).")
                confirm_t0 = _time()
                confirm_click_result = device_action.click_with_metrics(
                    confirm_use.get("click_target"),
                    text=f"{log_tag} Confirm planned item use.",
                )
                flow["timing_confirm_use"] = round(_time() - confirm_t0, 3)
                flow["confirm_use_click_result"] = confirm_click_result
                flow["confirm_use_clicked"] = bool(confirm_click_result.get("clicked"))
                if not flow["confirm_use_clicked"]:
                    if not flow["reason"]:
                        flow["reason"] = "failed_to_click_confirm_use"

                if flow["confirm_use_clicked"] and handle_followup:
                    # full mode: poll for the followup confirmation popup.
                    # On ADB the popup may take a moment to appear after
                    # the first confirm-use click, so retry several times.
                    post_confirm_controls_t0 = _time()
                    followup_confirm = {}
                    post_confirm_controls = {}
                    for _followup_poll in range(6):
                        sleep(0.3)
                        device_action.flush_screenshot_cache()
                        post_confirm_controls = detect_inventory_controls()
                        candidates = post_confirm_controls.get("confirm_candidates") or {}
                        candidate = (
                            candidates.get("inventory_use_training_items")
                            or candidates.get("shop_confirm")
                            or {}
                        )
                        if candidate.get("passed_threshold"):
                            followup_confirm = candidate
                            break
                    flow["timing_post_confirm_controls"] = round(_time() - post_confirm_controls_t0, 3)
                    flow["post_confirm_controls"] = post_confirm_controls
                    flow["followup_confirm_polls"] = _followup_poll + 1
                    flow["followup_confirm_visible"] = bool(followup_confirm.get("passed_threshold"))
                    if flow["followup_confirm_visible"] and followup_confirm.get("click_target"):
                        flow["followup_confirm_entry"] = followup_confirm
                        followup_t0 = _time()
                        followup_click_result = device_action.click_with_metrics(
                            followup_confirm.get("click_target"),
                            text=f"{log_tag} Confirm item use follow-up prompt.",
                        )
                        flow["timing_followup_confirm"] = round(_time() - followup_t0, 3)
                        flow["followup_confirm_click_result"] = followup_click_result
                        flow["followup_confirm_clicked"] = bool(followup_click_result.get("clicked"))

                        if flow["followup_confirm_clicked"]:
                            # Item-use animations can outlast the original fixed
                            # delay, so keep polling the lower close controls.
                            close_poll_t0 = _time()
                            post_use_close = _wait_for_post_item_use_close_button()
                            flow["timing_post_animation_close"] = round(_time() - close_poll_t0, 3)
                            flow["post_animation_close_clicked"] = bool(post_use_close.get("clicked"))
                            flow["post_animation_close_result"] = post_use_close
                            flow["post_animation_close_saw_inventory"] = bool(post_use_close.get("saw_inventory_screen"))
                            sleep(0.2)

                    verify_t0 = _time()
                    device_action.flush_screenshot_cache()
                    still_open, _, verify_checks = detect_inventory_screen()
                    flow["timing_verify_closed"] = round(_time() - verify_t0, 3)
                    flow["verification_checks"] = verify_checks
                    flow["closed"] = bool((flow.get("post_animation_close_result") or {}).get("closed"))
                    if not flow["closed"] and still_open:
                        close_t0 = _time()
                        close_result = close_training_items_inventory()
                        flow["timing_close"] = round(_time() - close_t0, 3)
                        flow["close_result"] = close_result
                        flow["closed"] = bool(close_result.get("closed"))
                    elif (
                        not flow["closed"]
                        and flow.get("post_animation_close_saw_inventory")
                        and not still_open
                    ):
                        flow["closed"] = True
                    if not flow["closed"] and not flow["reason"]:
                        flow["reason"] = "inventory_did_not_close_after_confirm"

                elif flow["confirm_use_clicked"] and not handle_followup:
                    # confirm_only mode: detect post-confirm controls but no followup
                    post_confirm_controls_t0 = _time()
                    flow["post_confirm_controls"] = detect_inventory_controls()
                    flow["timing_post_confirm_controls"] = round(_time() - post_confirm_controls_t0, 3)

            elif not commit_clicks:
                # dry_run mode: close inventory to avoid consuming items
                info(
                    f"{log_tag} Closing inventory without confirm-use (commit_mode={commit_mode}). "
                    "Destructive clicks are skipped in this mode."
                )
                close_t0 = _time()
                close_result = close_training_items_inventory()
                flow["timing_close"] = round(_time() - close_t0, 3)
                flow["close_result"] = close_result
                flow["closed"] = bool(close_result.get("closed"))
                if not flow["closed"] and not flow["reason"]:
                    flow["reason"] = "failed_to_close_inventory"

            # -- Tally increments --
            increments_requested = len(requested_items)
            flow["increments_requested"] = increments_requested
            flow["increments_clicked"] = sum(1 for attempt in flow["increment_attempts"] if attempt.get("clicked"))
            flow["increments_simulated"] = sum(1 for attempt in flow["increment_attempts"] if attempt.get("simulated"))

            # -- Success criteria per mode --
            items_actionable = (
                not flow["missing_items"]
                and not flow["missing_increment_targets"]
            )
            if commit_mode == "dry_run":
                # dry_run: items were found and actionable, inventory closed.
                # No clicks were performed, so we do not check increments_clicked
                # or confirm_use_available (confirm-use stays disabled without
                # increment clicks, which is expected).
                flow["success"] = items_actionable and flow["closed"]
            elif commit_mode == "confirm_only":
                flow["success"] = (
                    items_actionable
                    and flow["increments_clicked"] == increments_requested
                    and flow["confirm_use_available"]
                    and flow["confirm_use_clicked"]
                )
            else:  # full
                followup_ok = (
                    not flow["followup_confirm_visible"]
                    or flow.get("followup_confirm_clicked")
                )
                flow["success"] = (
                    (
                        flow.get("graceful_noop")
                        or (
                            items_actionable
                            and flow["increments_clicked"] == increments_requested
                            and flow["confirm_use_available"]
                            and flow["confirm_use_clicked"]
                            and followup_ok
                            and flow["closed"]
                        )
                    )
                )
        else:
            result["trackblazer_inventory_controls"] = controls
    finally:
        flow["timing_total"] = round(_time() - t_total, 3)
        result["trackblazer_inventory_flow"] = flow
        result["success"] = bool(flow.get("success"))
        record_runtime_ocr_debug(
            "trackblazer_inventory_execute_items",
            extra={
                "region_key": "MANT_INVENTORY_ITEMS_REGION",
                "region_xywh": list(constants.MANT_INVENTORY_ITEMS_REGION),
                "commit_mode": commit_mode,
                "items_detected": result["trackblazer_inventory_summary"].get("items_detected", []),
                "controls": result["trackblazer_inventory_controls"],
                "flow": flow,
            },
        )
        result["ocr_runtime_debug"] = snapshot_runtime_ocr_debug()
        result["inventory_ocr_debug_entries"] = _build_trackblazer_inventory_debug_entries(
            flow,
            result["trackblazer_inventory_controls"],
            result["trackblazer_inventory"],
        )

    info(
        f"{log_tag} execute_training_items timing (commit_mode={commit_mode}): "
        f"total={flow.get('timing_total', '?')}s "
        f"open={flow.get('timing_open', '-')} scan={flow.get('timing_scan', '-')} "
        f"increments={flow.get('timing_increments', '-')} controls={flow.get('timing_controls', '-')} "
        f"close={flow.get('timing_close', '-')}"
    )
    return result



def detect_use_training_items_button(threshold=0.8):
    """Check if the 'Use Training Items' button is visible on screen.

    Returns the match location tuple (x, y, w, h) or None.
    """
    template = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("use_training_items")
    if not template:
        return None
    match = device_action.locate(template, min_search_time=get_secs(1))
    return match


def detect_shop_entry_button(threshold=0.8):
    """Return the best available Trackblazer shop entry method and target.

    The returned key is a method name such as ``refresh_dialog`` or
    ``lobby_button`` so future callers can branch on the entry path cleanly.
    """
    state = inspect_shop_entry_state(threshold=threshold)
    best_method = state.get("best_method") or {}
    if not best_method.get("matched"):
        return None, None
    return best_method.get("method"), best_method.get("entry")


def detect_shop_screen(threshold=0.7):
    """Check whether the Trackblazer shop screen is currently open."""
    region_ltrb = _trackblazer_ui_region()
    screenshot = device_action.screenshot(region_ltrb=region_ltrb)
    checks = []

    for key in (*_shop_confirm_template_keys(), "shop_aftersale_close"):
        template_path = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get(key)
        if not template_path:
            continue
        entry = _best_match_entry(
            template_path,
            region_ltrb=region_ltrb,
            threshold=threshold,
            screenshot=screenshot,
        )
        entry["key"] = key
        checks.append(entry)
        if entry.get("passed_threshold"):
            return True, entry, checks

    back_template = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("use_back")
    if back_template:
        back_entry = _best_match_entry(
            back_template,
            region_ltrb=region_ltrb,
            threshold=threshold,
            screenshot=screenshot,
        )
        back_entry["key"] = "use_back"
        checks.append(back_entry)
        if back_entry.get("passed_threshold"):
            return True, back_entry, checks

    return False, None, checks


def _start_trackblazer_shop_coins_ocr_task():
    """Start shop-coin OCR without blocking the rest of the shop scan."""
    result_queue = Queue(maxsize=1)
    task = {
        "started": True,
        "thread": None,
        "queue": result_queue,
        "started_at": _time(),
    }

    def _run():
        t0 = _time()
        payload = {
            "shop_coins": -1,
            "error": "",
            "timing": {
                "ocr_wall": 0.0,
            },
        }
        try:
            payload["shop_coins"] = get_trackblazer_shop_coins()
        except Exception as exc:
            payload["error"] = str(exc)
            warning(f"[TB_SHOP] shop coin OCR failed: {exc}")
        payload["timing"]["ocr_wall"] = round(_time() - t0, 4)
        result_queue.put(payload)

    thread = threading.Thread(target=_run, daemon=True)
    task["thread"] = thread
    thread.start()
    return task


def _resolve_trackblazer_shop_coins_ocr_task(task):
    """Wait for a background shop-coin OCR task and return its result."""
    result = {
        "shop_coins": -1,
        "error": "",
        "timing": {},
    }
    if not task:
        return result

    thread = task.get("thread")
    join_t0 = _time()
    if thread is not None:
        thread.join()
    wait_elapsed = _time() - join_t0

    result_queue = task.get("queue")
    if result_queue is not None and not result_queue.empty():
        result.update(result_queue.get())

    timing = dict(result.get("timing") or {})
    timing["join_wait"] = round(wait_elapsed, 4)
    timing["lifetime"] = round(_time() - float(task.get("started_at") or _time()), 4)
    result["timing"] = timing
    return result


def enter_shop(threshold=0.8, read_shop_coins=True):
    """Enter the Trackblazer shop using the best currently supported method.

    Supports refresh-dialog and lobby entry buttons when their templates are
    visible. Returns timing and verification data for operator-console review.
    """
    t_total = _time()
    timing = {}

    t0 = _time()
    shop_state = inspect_shop_entry_state(threshold=threshold)
    timing["detect_entry"] = round(_time() - t0, 4)

    best_method = shop_state.get("best_method") or {}
    method_name = best_method.get("method")
    entry = best_method.get("entry") or {}

    if not best_method.get("matched") or not entry.get("click_target"):
        timing["total"] = round(_time() - t_total, 4)
        return {
            "entered": False,
            "clicked": False,
            "method": method_name,
            "reason": "no_supported_shop_entry_detected",
            "shop_check": shop_state,
            "click_metrics": None,
            "timing": timing,
        }

    t0 = _time()
    click_metrics = device_action.click_with_metrics(entry["click_target"])
    timing["click_total"] = round(_time() - t0, 4)
    timing["click_breakdown"] = click_metrics
    clicked = bool(click_metrics.get("clicked"))

    t0 = _time()
    sleep(0.35)
    timing["sleep"] = round(_time() - t0, 4)

    t0 = _time()
    shop_open, verification_entry, verification_checks = detect_shop_screen(
        threshold=max(0.7, threshold - 0.1),
    ) if clicked else (False, None, [])
    timing["verify"] = round(_time() - t0, 4)
    shop_coins = -1
    if shop_open and read_shop_coins:
        t0 = _time()
        shop_coins = get_trackblazer_shop_coins()
        timing["read_shop_coins"] = round(_time() - t0, 4)
    timing["total"] = round(_time() - t_total, 4)

    info(f"[TB_SHOP] enter shop timing: {timing}")
    return {
        "entered": bool(clicked and shop_open),
        "clicked": bool(click_metrics.get("clicked")),
        "method": method_name,
        "reason": (
            f"clicked_{method_name}_shop"
            if clicked and shop_open else
            ("shop_verification_failed" if clicked else "click_failed")
        ),
        "shop_coins": shop_coins,
        "shop_check": shop_state,
        "click_metrics": click_metrics,
        "verification": verification_entry,
        "verification_checks": verification_checks,
        "timing": timing,
    }


def close_trackblazer_shop(threshold=0.8, verify_threshold=0.75):
    """Exit the Trackblazer shop without purchasing anything."""
    t_total = _time()
    timing = {}
    region_ltrb = _trackblazer_ui_region()
    t0 = _time()
    screenshot = device_action.screenshot(region_ltrb=region_ltrb)
    timing["screenshot"] = round(_time() - t0, 4)

    attempt_entries = []
    last_clicked_entry = None
    last_click_metrics = None
    last_verification_entry = None
    last_verification_checks = []
    attempts = [
        ("shop_aftersale_close", constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_aftersale_close"), _INVERSE_GLOBAL_SCALE),
        ("use_back", constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("use_back"), _INVERSE_GLOBAL_SCALE),
        ("close_btn", "assets/buttons/close_btn.png", 1.0),
        ("back_btn", "assets/buttons/back_btn.png", 1.0),
    ]
    t0 = _time()
    for key, template_path, template_scaling in attempts:
        if not template_path:
            continue
        entry = _best_match_entry(
            template_path,
            region_ltrb=region_ltrb,
            threshold=threshold,
            template_scaling=template_scaling,
            screenshot=screenshot,
        )
        entry["key"] = key
        attempt_entries.append(entry)
        if not entry.get("passed_threshold"):
            continue
        timing["match_attempts"] = round(_time() - t0, 4)
        timing["matched_key"] = key

        t0 = _time()
        click_metrics = device_action.click_with_metrics(
            entry["click_target"],
            text=f"[TB_SHOP] Close shop via {key}.",
        )
        clicked = bool(click_metrics.get("clicked"))
        timing["click_total"] = round(_time() - t0, 4)
        timing["click_breakdown"] = click_metrics
        last_clicked_entry = entry
        last_click_metrics = click_metrics

        t0 = _time()
        sleep(0.2)
        timing["sleep"] = round(_time() - t0, 4)

        t0 = _time()
        still_open, verification_entry, verification_checks = detect_shop_screen(threshold=verify_threshold)
        timing["verify"] = round(_time() - t0, 4)
        last_verification_entry = verification_entry
        last_verification_checks = verification_checks
        if not still_open:
            timing["total"] = round(_time() - t_total, 4)
            info(f"[TB_SHOP] close timing: {timing}")
            return {
                "closed": True,
                "clicked": True,
                "attempt": entry,
                "attempts": attempt_entries,
                "verification": verification_entry,
                "verification_checks": verification_checks,
                "timing": timing,
            }
        info(f"[TB_SHOP] close: {key} matched (score={entry.get('score')}) but shop still open after click; trying next template.")
        screenshot = device_action.screenshot(region_ltrb=region_ltrb)

    timing["match_attempts"] = round(_time() - t0, 4)
    timing["total"] = round(_time() - t_total, 4)
    if last_clicked_entry:
        info(f"[TB_SHOP] close timing: click_did_not_close {timing}")
    else:
        info(f"[TB_SHOP] close timing: no_match {timing}")
    return {
        "closed": False,
        "clicked": bool(last_click_metrics and last_click_metrics.get("clicked")),
        "attempt": last_clicked_entry,
        "attempts": attempt_entries,
        "verification": last_verification_entry,
        "verification_checks": last_verification_checks,
        "timing": timing,
    }


def inspect_shop_entry_state(threshold=0.8):
    """Collect a debug-friendly summary of Trackblazer shop entry detection.

    This is intentionally method-oriented so the shop flow can support multiple
    entry paths: the refresh dialog now, and a direct lobby button later.
    """
    def _entry_template(key):
        template_path = constants.TRACKBLAZER_SHOP_ENTRY_TEMPLATES.get(key)
        if not template_path:
            return None
        template_file = Path(template_path)
        if not template_file.is_absolute():
            template_file = Path.cwd() / template_file
        if not template_file.exists():
            return None
        return template_path

    def _shop_method_summary(method_name, region_ltrb, parts, required_keys, template_scaling):
        screenshot = device_action.screenshot(region_ltrb=region_ltrb)
        checks = {}
        for key, template_path in parts.items():
            entry = _best_match_entry(
                template_path,
                region_ltrb=region_ltrb,
                threshold=threshold,
                template_scaling=template_scaling,
                screenshot=screenshot,
            )
            entry["key"] = key
            checks[key] = entry

        missing_required = [
            key for key in required_keys
            if not (checks.get(key) or {}).get("passed_threshold")
        ]
        scored_checks = [entry for entry in checks.values() if entry.get("score") is not None]
        best_entry = max(scored_checks, key=lambda entry: entry.get("score") or 0.0) if scored_checks else None
        matched = not missing_required
        method_summary = {
            "method": method_name,
            "matched": matched,
            "ready": matched,
            "entry": (
                checks.get("shop_refresh_shop")
                or checks.get("shop_enter_lobby")
                or checks.get("shop_enter_summer_lobby")
            ),
            "dismiss": checks.get("shop_refresh_cancel"),
            "best_match": best_entry,
            "region_ltrb": [int(v) for v in region_ltrb],
            "required_keys": list(required_keys),
            "missing_required": missing_required,
            "checks": checks,
        }
        return method_summary

    def _refresh_dialog_summary():
        dialog_template_keys = ("shop_sale_popup", "shop_refresh_dialog")

        def _dialog_button_entry(key, template_path, game_region, game_screenshot, dialog_region):
            fallback = _best_match_entry(
                template_path,
                region_ltrb=game_region,
                threshold=threshold,
                template_scaling=_INVERSE_GLOBAL_SCALE,
                screenshot=game_screenshot,
            )
            fallback["key"] = key

            matches = device_action.match_template(
                template_path,
                game_screenshot,
                threshold=threshold,
                template_scaling=_INVERSE_GLOBAL_SCALE,
            )
            if not matches:
                return fallback

            dialog_left, dialog_top, dialog_right, dialog_bottom = dialog_region
            dialog_mid_y = dialog_top + (dialog_bottom - dialog_top) * 0.55
            candidates = []
            for match in matches:
                match_x, match_y, match_w, match_h = [int(v) for v in match]
                center_x = int(game_region[0] + match_x + match_w // 2)
                center_y = int(game_region[1] + match_y + match_h // 2)
                if dialog_left <= center_x <= dialog_right and dialog_mid_y <= center_y <= dialog_bottom:
                    candidates.append((match_x, match_y, match_w, match_h, center_x, center_y))

            if not candidates:
                return fallback

            match_x, match_y, match_w, match_h, center_x, center_y = max(
                candidates,
                key=lambda match: (match[5], match[4]),
            )
            return {
                "template": template_path,
                "threshold": threshold,
                "matched": True,
                "passed_threshold": True,
                "score": None,
                "location": [int(match_x), int(match_y)],
                "size": [int(match_w), int(match_h)],
                "click_target": (int(center_x), int(center_y)),
                "selection_reason": "dialog_lower_row_candidate",
                "candidate_count": len(candidates),
                "key": key,
                "search_image_path": fallback.get("search_image_path"),
                "region_ltrb": fallback.get("region_ltrb"),
            }

        dialog_templates = {
            key: _entry_template(key)
            for key in dialog_template_keys
            if _entry_template(key)
        }
        if not dialog_templates:
            return {
                "method": "refresh_dialog",
                "matched": False,
                "ready": False,
                "entry": None,
                "dismiss": None,
                "best_match": None,
                "region_ltrb": [int(v) for v in _trackblazer_ui_region()],
                "required_keys": ["dialog", "shop_refresh_shop", "shop_refresh_cancel"],
                "missing_required": ["dialog", "shop_refresh_shop", "shop_refresh_cancel"],
                "checks": {},
                "reason": "templates_missing",
            }

        game_region = _trackblazer_ui_region()
        game_screenshot = device_action.screenshot(region_ltrb=game_region)
        checks = {}
        dialog_candidates = []
        for dialog_key, dialog_template in dialog_templates.items():
            dialog_entry = _best_match_entry(
                dialog_template,
                region_ltrb=game_region,
                threshold=threshold,
                template_scaling=_INVERSE_GLOBAL_SCALE,
                screenshot=game_screenshot,
            )
            dialog_entry["key"] = dialog_key
            checks[dialog_key] = dialog_entry
            if dialog_entry.get("score") is not None:
                dialog_candidates.append(dialog_entry)

        passed_dialog_candidates = [
            entry for entry in dialog_candidates if entry.get("passed_threshold")
        ]
        dialog_entry = None
        if passed_dialog_candidates:
            dialog_entry = max(
                passed_dialog_candidates,
                key=lambda entry: entry.get("score") or 0.0,
            )
        elif dialog_candidates:
            dialog_entry = max(
                dialog_candidates,
                key=lambda entry: entry.get("score") or 0.0,
            )

        dialog_region = game_region
        if dialog_entry and dialog_entry.get("passed_threshold") and dialog_entry.get("location") and dialog_entry.get("size"):
            dialog_x, dialog_y = dialog_entry["location"]
            dialog_w, dialog_h = dialog_entry["size"]
            dialog_region = (
                int(game_region[0] + dialog_x),
                int(game_region[1] + dialog_y),
                int(game_region[0] + dialog_x + dialog_w),
                int(game_region[1] + dialog_y + dialog_h),
            )

        for key in ("shop_refresh_cancel", "shop_refresh_shop"):
            template_path = _entry_template(key)
            if not template_path:
                continue
            entry = _dialog_button_entry(
                key,
                template_path,
                game_region,
                game_screenshot,
                dialog_region,
            )
            checks[key] = entry

        missing_required = []
        if not (dialog_entry or {}).get("passed_threshold"):
            missing_required.append("dialog")
        for key in ("shop_refresh_shop", "shop_refresh_cancel"):
            if not (checks.get(key) or {}).get("passed_threshold"):
                missing_required.append(key)
        scored_checks = [entry for entry in checks.values() if entry.get("score") is not None]
        best_entry = max(scored_checks, key=lambda entry: entry.get("score") or 0.0) if scored_checks else None
        matched = not missing_required
        return {
            "method": "refresh_dialog",
            "matched": matched,
            "ready": matched,
            "entry": checks.get("shop_refresh_shop"),
            "dismiss": checks.get("shop_refresh_cancel"),
            "dialog": dialog_entry,
            "best_match": best_entry,
            "region_ltrb": [int(v) for v in dialog_region],
            "required_keys": ["dialog", "shop_refresh_shop", "shop_refresh_cancel"],
            "missing_required": missing_required,
            "checks": checks,
        }

    methods = {}

    def _single_template_method(method_name, template_key):
        template_path = _entry_template(template_key)
        if template_path:
            template_scaling = (
                _INVERSE_GLOBAL_SCALE
                if "assets/trackblazer/" in str(template_path).replace("\\", "/")
                else 1.0
            )
            region_ltrb = (
                _trackblazer_ui_region()
                if template_key == "shop_enter_summer_lobby"
                else constants.MANT_SHOP_BUTTON_BBOX
            )
            return _shop_method_summary(
                method_name,
                region_ltrb,
                {template_key: template_path},
                required_keys=(template_key,),
                template_scaling=template_scaling,
            )
        return {
            "method": method_name,
            "matched": False,
            "ready": False,
            "entry": None,
            "dismiss": None,
            "best_match": None,
            "region_ltrb": [int(v) for v in constants.MANT_SHOP_BUTTON_BBOX],
            "required_keys": [template_key],
            "missing_required": [template_key],
            "checks": {},
            "reason": "templates_missing",
        }

    methods["refresh_dialog"] = _refresh_dialog_summary()
    methods["lobby_button"] = _single_template_method("lobby_button", "shop_enter_lobby")
    methods["summer_lobby_button"] = _single_template_method("summer_lobby_button", "shop_enter_summer_lobby")

    ready_methods = [entry for entry in methods.values() if entry.get("ready")]
    scored_methods = [entry for entry in methods.values() if (entry.get("best_match") or {}).get("score") is not None]
    best_method = None
    if ready_methods:
        best_method = max(
            ready_methods,
            key=lambda entry: (entry.get("best_match") or {}).get("score") or 0.0,
        )
    elif scored_methods:
        best_method = max(
            scored_methods,
            key=lambda entry: (entry.get("best_match") or {}).get("score") or 0.0,
        )

    return {
        "threshold": threshold,
        "matched": bool(best_method and best_method.get("matched")),
        "entry_method": best_method.get("method") if best_method else None,
        "best_method": best_method,
        "methods": methods,
    }


def check_trackblazer_shop_inventory(
    threshold=0.7,
    checkbox_threshold=0.8,
    confirm_threshold=0.7,
    max_reset_swipes=4,
    max_forward_swipes=8,
    trigger="manual_console",
):
    """Enter the Trackblazer shop, scan all visible items, then back out."""
    clear_runtime_ocr_debug()
    t_total = _time()
    flow = {
        "trigger": str(trigger or "manual_console"),
        "execution_intent": bot.get_execution_intent(),
        "read_only": True,
        "safety_rule": "never_click_shop_checkbox_or_confirm",
        "entered": False,
        "closed": False,
        "entry_result": None,
        "scan_result": None,
        "close_result": None,
        "shop_coins": -1,
        "pages": [],
        "all_items": [],
        "stop_reason": "",
        "reason": "",
    }
    result = {
        "trackblazer_shop_items": [],
        "trackblazer_shop_summary": {
            "items_detected": [],
            "page_count": 0,
            "stop_reason": "",
            "shop_coins": -1,
        },
        "trackblazer_shop_flow": flow,
        "ocr_runtime_debug": {},
        "inventory_ocr_debug_entries": [],
        "success": False,
    }
    coin_ocr_task = None

    try:
        t0 = _time()
        entry_result = enter_shop(
            threshold=max(0.8, threshold),
            read_shop_coins=False,
        )
        flow["timing_open"] = round(_time() - t0, 3)
        flow["entry_result"] = entry_result
        flow["entered"] = bool(entry_result.get("entered"))
        flow["shop_coins"] = entry_result.get("shop_coins", -1)
        if not flow["entered"]:
            flow["reason"] = entry_result.get("reason") or "failed_to_enter_shop"
            if entry_result.get("clicked"):
                warning(
                    "[TB_SHOP] Shop entry button was clicked but verification failed; "
                    "attempting recovery close in case shop is actually open."
                )
                t0 = _time()
                close_result = close_trackblazer_shop()
                flow["timing_recovery_close"] = round(_time() - t0, 3)
                flow["recovery_close_result"] = close_result
                flow["closed"] = bool(close_result.get("closed"))
            return result

        coin_ocr_task = _start_trackblazer_shop_coins_ocr_task()
        flow["shop_coins_ocr_started"] = True

        t0 = _time()
        scan_result = scan_all_trackblazer_shop_items(
            threshold=threshold,
            checkbox_threshold=checkbox_threshold,
            confirm_threshold=confirm_threshold,
            max_reset_swipes=max_reset_swipes,
            max_forward_swipes=max_forward_swipes,
        )
        flow["timing_scan"] = round(_time() - t0, 3)
        flow["scan_result"] = scan_result
        flow["scan_timing"] = (scan_result.get("flow") or {}).get("timing") or {}
        flow["pages"] = [page.get("visible_items") for page in (scan_result.get("pages") or [])]
        flow["all_items"] = list(scan_result.get("all_items") or [])
        flow["purchasable_items"] = list(scan_result.get("purchasable_items") or [])
        flow["stop_reason"] = (scan_result.get("flow") or {}).get("stop_reason") or ""
        result["trackblazer_shop_items"] = list(flow["purchasable_items"])
        result["trackblazer_shop_summary"] = {
            "items_detected": list(flow["all_items"]),
            "purchasable_items": list(flow["purchasable_items"]),
            "page_count": len(scan_result.get("pages") or []),
            "stop_reason": flow["stop_reason"],
            "shop_coins": flow["shop_coins"],
        }

        t0 = _time()
        coin_ocr_result = _resolve_trackblazer_shop_coins_ocr_task(coin_ocr_task)
        flow["timing_shop_coins"] = round(_time() - t0, 3)
        flow["shop_coins"] = int(coin_ocr_result.get("shop_coins", -1))
        flow["shop_coins_ocr"] = coin_ocr_result
        result["trackblazer_shop_summary"]["shop_coins"] = flow["shop_coins"]
        entry_result["shop_coins"] = flow["shop_coins"]
        entry_timing = dict(entry_result.get("timing") or {})
        entry_timing["read_shop_coins_parallel"] = coin_ocr_result.get("timing") or {}
        entry_result["timing"] = entry_timing

        t0 = _time()
        close_result = close_trackblazer_shop()
        flow["timing_close"] = round(_time() - t0, 3)
        flow["close_result"] = close_result
        flow["closed"] = bool(close_result.get("closed"))
        if not flow["closed"] and not flow["reason"]:
            flow["reason"] = "failed_to_close_shop"

        flow["success"] = bool(flow["entered"] and flow["closed"] and flow["all_items"])
        result["success"] = bool(flow["success"])
    finally:
        if coin_ocr_task and "shop_coins_ocr" not in flow:
            coin_ocr_result = _resolve_trackblazer_shop_coins_ocr_task(coin_ocr_task)
            flow["shop_coins_ocr"] = coin_ocr_result
            flow["shop_coins"] = int(coin_ocr_result.get("shop_coins", flow.get("shop_coins", -1)))
            result["trackblazer_shop_summary"]["shop_coins"] = flow["shop_coins"]
            entry_result = flow.get("entry_result") or {}
            if entry_result:
                entry_result["shop_coins"] = flow["shop_coins"]
                entry_timing = dict(entry_result.get("timing") or {})
                entry_timing["read_shop_coins_parallel"] = coin_ocr_result.get("timing") or {}
                entry_result["timing"] = entry_timing
        flow["timing_total"] = round(_time() - t_total, 3)
        flow["timing_controls"] = flow.get("timing_close")
        scan_result = flow.get("scan_result") or {}
        scan_flow = scan_result.get("flow") or {}
        flow["timing_reset_swipes"] = round(
            sum((swipe.get("duration", 0.0) + swipe.get("settle_seconds", 0.0)) for swipe in (scan_flow.get("reset_swipes") or [])),
            3,
        )
        flow["timing_forward_swipes"] = round(
            sum((swipe.get("duration", 0.0) + swipe.get("settle_seconds", 0.0)) for swipe in (scan_flow.get("forward_swipes") or [])),
            3,
        )
        result["trackblazer_shop_flow"] = flow
        record_runtime_ocr_debug(
            "trackblazer_shop_manual_check",
            extra={
                "items_detected": result["trackblazer_shop_summary"].get("items_detected", []),
                "page_count": result["trackblazer_shop_summary"].get("page_count"),
                "stop_reason": result["trackblazer_shop_summary"].get("stop_reason"),
                "shop_coins": result["trackblazer_shop_summary"].get("shop_coins"),
                "flow": flow,
            },
        )
        result["ocr_runtime_debug"] = snapshot_runtime_ocr_debug()
        result["inventory_ocr_debug_entries"] = _build_trackblazer_shop_debug_entries(
            flow,
            result["ocr_runtime_debug"],
        )

    info(
        f"[TB_SHOP] manual shop check timing: total={flow.get('timing_total', '?')}s "
        f"open={flow.get('timing_open', '-')} scan={flow.get('timing_scan', '-')} "
        f"close={flow.get('timing_close', '-')}"
    )
    return result


# ---------------------------------------------------------------------------
# Rival-race detection for the Trackblazer race selection screen.
# ---------------------------------------------------------------------------

# Spatial offset from a rival-racer label to its 2-aptitude stars.
# Measured empirically: stars are ~77px right, ~71px below the rival label.
_RIVAL_TO_APTITUDE_DX = 77
_RIVAL_TO_APTITUDE_DY = 71
# How far the aptitude match can deviate from the expected position.
_APTITUDE_PAIR_TOLERANCE = 40
_APTITUDE_MATCH_THRESHOLD = 0.7
_RIVAL_BUTTON_INDICATOR_THRESHOLD = 0.75

# VS icon templates that appear on the race button when a rival race exists.
# Summer and normal lobby have different assets.
_RIVAL_BUTTON_INDICATORS = [
    constants.TRACKBLAZER_RACE_TEMPLATES["summer_rival_race_button"],
    constants.TRACKBLAZER_RACE_TEMPLATES["rival_race_button"],
    constants.TRACKBLAZER_RACE_TEMPLATES["rival_race_button_vs"],
]


def check_climax_locked_race_button():
    """Detect the tiny lock overlay shown on climax training turns."""
    template_path = constants.TRACKBLAZER_RACE_TEMPLATES.get("climax_race_locked")
    if not template_path:
        return False
    screenshot = device_action.screenshot(region_ltrb=constants.TRACKBLAZER_CLIMAX_RACE_LOCK_BBOX)
    matches = device_action.match_template(
        template_path,
        screenshot,
        threshold=0.7,
        template_scaling=_INVERSE_GLOBAL_SCALE,
    )
    if matches:
        info("[TB_RIVAL] Climax lock detected on race button.")
        return True
    return False


def check_rival_race_indicator(state_obj=None):
    """Check the race button area for the VS rival-race indicator icon.

    This is a cheap pre-check on the main lobby screen.  If the VS icon is
    not present on the race button, there is no rival race this turn and we
    can skip the expensive scout entirely.

    Returns True if any rival indicator is detected.
    """
    state_obj = state_obj if isinstance(state_obj, dict) else {}
    if state_obj.get("trackblazer_climax") and check_climax_locked_race_button():
        debug("[TB_RIVAL] Skipping rival indicator check: climax race button is locked.")
        return False
    screenshot = device_action.screenshot(region_ltrb=constants.SCREEN_BOTTOM_BBOX)
    for template_path in _RIVAL_BUTTON_INDICATORS:
        matches = device_action.match_template(
            template_path,
            screenshot,
            threshold=_RIVAL_BUTTON_INDICATOR_THRESHOLD,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
        if matches:
            info(f"[TB_RIVAL] Rival race indicator found on race button ({template_path}).")
            return True
    debug("[TB_RIVAL] No rival race indicator on race button.")
    return False


def find_rival_races_with_aptitude(screenshot=None):
    """Scan the visible race list for rival races with 2 matching aptitudes.

    Must be called while the race list UI is open.  Searches for all
    'RIVAL RACER!' labels, then checks each one for a nearby 2-aptitude
    star icon.  Only rivals with the aptitude confirmation are returned.

    Returns a list of dicts, each with:
        rival: (x, y, w, h) of the rival label
        aptitude: (x, y, w, h) of the paired aptitude stars, or None
        click_target: (x, y) absolute coords to click (the aptitude stars)

    Both templates use inverse global scale (native-res Trackblazer assets).

    Important: the race list often shows only ~2 rows at once, and the bottom
    row can be partially occluded. Seeing ``rival_racer`` without the aptitude
    stars below it should not immediately disqualify the row forever; a later
    refinement should rescan after a small scroll when the rival marker is near
    the list edge.
    """
    rival_template = constants.TRACKBLAZER_RACE_TEMPLATES["rival_racer"]
    aptitude_template = constants.TRACKBLAZER_RACE_TEMPLATES["race_recommend_2_aptitudes"]

    if screenshot is None:
        screenshot = device_action.screenshot(region_ltrb=constants.RACE_LIST_BOX_BBOX)

    rival_matches = device_action.match_template(
        rival_template,
        screenshot,
        threshold=_RIVAL_RACE_MATCH_THRESHOLD,
        template_scaling=_INVERSE_GLOBAL_SCALE,
    )
    if not rival_matches:
        return []

    aptitude_matches = device_action.match_template(
        aptitude_template,
        screenshot,
        threshold=_APTITUDE_MATCH_THRESHOLD,
        template_scaling=_INVERSE_GLOBAL_SCALE,
    )

    results = []
    for rival in rival_matches:
        rx, ry = rival[0], rival[1]
        # Expected aptitude position relative to this rival label.
        expected_ax = rx + _RIVAL_TO_APTITUDE_DX
        expected_ay = ry + _RIVAL_TO_APTITUDE_DY

        paired_apt = None
        for apt in aptitude_matches:
            ax, ay = apt[0], apt[1]
            if (abs(ax - expected_ax) < _APTITUDE_PAIR_TOLERANCE
                    and abs(ay - expected_ay) < _APTITUDE_PAIR_TOLERANCE):
                paired_apt = apt
                break

        if paired_apt:
            # Click target is the centre of the aptitude stars.
            apt_cx = paired_apt[0] + paired_apt[2] // 2
            apt_cy = paired_apt[1] + paired_apt[3] // 2
            results.append({
                "rival": rival,
                "aptitude": paired_apt,
                "click_target": (apt_cx, apt_cy),
            })
            debug(
                f"[TB_RIVAL] Paired rival at ({rx},{ry}) with aptitude at "
                f"({paired_apt[0]},{paired_apt[1]})"
            )
        else:
            debug(
                f"[TB_RIVAL] Rival at ({rx},{ry}) has no 2-aptitude match nearby "
                f"(expected ~({expected_ax},{expected_ay}))"
            )

    return results


def scout_rival_race():
    """Open the race list, scan for a rival race with good aptitudes, back out.

    Returns a dict:
        rival_found: bool — at least one rival with 2 aptitudes was found
        match: first paired result dict, or None
        all_matches: list of all paired results across all scroll pages
        rivals_without_aptitude: count of rival labels seen without aptitude

    This is non-committing — always backs out of the race list.
    Checks the race button VS indicator first as a cheap early-exit.
    """
    _no_rival = {"rival_found": False, "match": None, "all_matches": [], "rivals_without_aptitude": 0}

    # Cheap pre-check: is the VS icon on the race button?
    if not check_rival_race_indicator():
        return _no_rival

    from utils.screenshot import are_screenshots_same
    from core.actions import go_to_racebox_top

    # Open race list.
    races_btn = device_action.locate_and_click(
        "assets/buttons/races_btn.png",
        min_search_time=get_secs(10),
        region_ltrb=constants.SCREEN_BOTTOM_BBOX,
    )
    if not races_btn:
        warning("[TB_RIVAL] Could not open race list for scouting.")
        return _no_rival

    sleep(1)

    # If the consecutive-race warning dialog appeared, click OK to proceed
    # past it into the race list. We detect the cancel button to confirm the
    # dialog is present, then click OK since scouting always backs out after.
    consecutive_warning_present = device_action.locate(
        "assets/buttons/cancel_btn.png", min_search_time=get_secs(1)
    )
    if consecutive_warning_present:
        device_action.locate_and_click(
            "assets/buttons/ok_btn.png", min_search_time=get_secs(1)
        )
    sleep(1)

    go_to_racebox_top()

    all_matches = []
    rivals_without_aptitude = 0
    for _ in range(10):
        screenshot = device_action.screenshot(region_ltrb=constants.RACE_LIST_BOX_BBOX)
        page_results = find_rival_races_with_aptitude(screenshot)
        all_matches.extend(page_results)

        # Also count rival labels that didn't pair, for logging.
        rival_template = constants.TRACKBLAZER_RACE_TEMPLATES["rival_racer"]
        rival_only = device_action.match_template(
            rival_template, screenshot,
            threshold=_RIVAL_RACE_MATCH_THRESHOLD,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
        rivals_without_aptitude += max(0, len(rival_only) - len(page_results))

        if all_matches:
            break  # Found at least one good rival, no need to scroll further.

        screenshot_before = screenshot
        device_action.swipe(
            constants.RACE_SCROLL_BOTTOM_MOUSE_POS,
            constants.RACE_SCROLL_TOP_MOUSE_POS,
        )
        device_action.click(constants.RACE_SCROLL_TOP_MOUSE_POS, duration=0)
        sleep(0.25)
        screenshot_after = device_action.screenshot(region_ltrb=constants.RACE_LIST_BOX_BBOX)
        if are_screenshots_same(screenshot_before, screenshot_after, diff_threshold=15):
            break  # Reached end of list.

    # Always back out.
    device_action.locate_and_click(
        "assets/buttons/back_btn.png",
        min_search_time=get_secs(2),
        region_ltrb=constants.SCREEN_BOTTOM_BBOX,
    )
    sleep(0.5)

    found = len(all_matches) > 0
    if found:
        info(
            f"[TB_RIVAL] Scout found {len(all_matches)} rival race(s) with 2 aptitudes "
            f"({rivals_without_aptitude} rival(s) without aptitude match)."
        )
    else:
        info(
            f"[TB_RIVAL] Scout: no suitable rival race. "
            f"({rivals_without_aptitude} rival(s) seen but without 2-aptitude match)."
        )
    return {
        "rival_found": found,
        "match": all_matches[0] if all_matches else None,
        "all_matches": all_matches,
        "rivals_without_aptitude": rivals_without_aptitude,
    }
