# scenarios/trackblazer.py
# Trackblazer (MANT) scenario-specific sub-routines.
# Inventory scanning, shop interaction, and item-use flows.

import utils.constants as constants
import utils.device_action_wrapper as device_action
import core.config as config
import core.bot as bot
from core.state import (
    _save_training_scan_debug_image,
    _build_trackblazer_inventory_debug_entries,
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
_HELD_LABEL_SEARCH_X = 120
_HELD_LABEL_SEARCH_WIDTH = 180
_HELD_QUANTITY_OFFSET_FROM_ICON_RIGHT = 122
_HELD_QUANTITY_SLICE_WIDTH = 52
_HELD_QUANTITY_SLICE_HEIGHT = 35
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


def _trackblazer_ui_region():
    return constants.GAME_WINDOW_BBOX


def _trackblazer_inventory_controls_region():
    left, top, right, bottom = constants.GAME_WINDOW_BBOX
    return (left, max(top, bottom - 220), right, bottom)


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


def _resolve_family_cluster(cluster, family_items, screenshot, threshold):
    left, top, right, bottom = _cluster_bounds(cluster, screenshot.shape)
    cluster_crop = screenshot[top:bottom, left:right].copy()
    effective_scale = device_action._effective_template_scale(_INVERSE_GLOBAL_SCALE)
    best_resolution = None
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
        if best_resolution is None or candidate["score"] > best_resolution["score"]:
            best_resolution = candidate
    return best_resolution


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
    for key in ("shop_confirm", "shop_aftersale_confirm_use_available", "shop_aftersale_confirm_use_unavailable"):
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
        generic_confirm = confirm_candidates.get("shop_confirm")
        if generic_confirm and generic_confirm.get("passed_threshold"):
            best_confirm = generic_confirm

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

        t0 = _time()
        sleep(0.15)
        timing["sleep"] = round(_time() - t0, 4)

        # Lightweight verification: just check if the header is still visible
        t0 = _time()
        header_template = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("use_training_items")
        if header_template:
            header_entry = _best_match_entry(header_template, threshold=threshold)
            still_open = header_entry["passed_threshold"]
            checks = [header_entry]
        else:
            still_open, _, checks = detect_inventory_screen(threshold=threshold)
        timing["verify"] = round(_time() - t0, 4)

        timing["total"] = round(_time() - t_total, 4)
        info(f"[TB_INV] close timing: {timing}")
        return {
            "closed": bool(clicked and not still_open),
            "clicked": bool(clicked),
            "attempt": entry,
            "attempts": attempt_entries,
            "verification_checks": checks,
            "timing": timing,
        }

    timing["match_attempts"] = round(_time() - t0, 4)
    timing["total"] = round(_time() - t_total, 4)
    info(f"[TB_INV] close timing: no_match — {timing}")
    return {
        "closed": False,
        "clicked": False,
        "attempt": None,
        "attempts": attempt_entries,
        "verification_checks": [],
        "timing": timing,
    }


def scan_training_items_inventory(threshold=0.8):
    """Scan the current screen for owned training item icons.

    This is a read-only sub-routine: it takes a screenshot of the inventory
    region and template-matches each known Trackblazer item icon.  It does
    NOT open any menus or click anything.

    For each detected item, the function also locates the increment (+)
    button on the same row by Y-coordinate proximity and records an
    absolute click target for it.

    Returns a dict keyed by item name with:
      - "detected": bool — whether the item icon was found
      - "category": str — item category from TRACKBLAZER_ITEM_CATEGORIES
      - "match_count": int — number of template matches (proxy for quantity rows)
      - "matches": list — raw match rectangles for debug
      - "increment_target": (abs_x, abs_y) | None — click target for the
        row's increment button, in absolute screen coordinates
      - "increment_match": [x, y, w, h] | None — raw region-relative match
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
    scan_timing = {
        "total": round(t_total_elapsed, 4),
        "held_ocr": round(t_held, 4),
        "templates": round(t_templates, 4),
        "families": round(t_families, 4),
        "families_resolved": families_resolved,
        "families_skipped": families_skipped,
        "items_detected": len(detected_items_for_pairing),
        "quantity_reads": len(held_quantities),
    }
    info(
        f"[TB_INV] scan timing: total={t_total_elapsed:.2f}s "
        f"held_ocr={t_held:.2f}s templates={t_templates:.2f}s "
        f"families={t_families:.2f}s (resolved={families_resolved} skipped={families_skipped}) "
        f"items_detected={len(detected_items_for_pairing)} quantity_reads={len(held_quantities)}"
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


def scan_trackblazer_shop_inventory(threshold=0.8, checkbox_threshold=0.8, confirm_threshold=0.8):
    """Scan the currently visible Trackblazer shop item rows without clicking.

    This is the first page-only framework. Scrolling will layer on top later.
    It detects visible item icons, resolves same-family variants row-by-row,
    pairs each row to the unchecked purchase checkbox on the right, and reads
    the current confirm button state. Costs are intentionally deferred.
    """
    t_total = _time()
    region_ltrb = _trackblazer_ui_region()
    screenshot = device_action.screenshot(region_ltrb=region_ltrb)
    icon_screenshot, icon_offset_x = _item_icon_search_crop(screenshot)
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
            (int(x + icon_offset_x), int(y), int(w), int(h))
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
    t_checkboxes = _time() - t0

    t0 = _time()
    confirm_template = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_confirm")
    confirm_entry = None
    if confirm_template:
        confirm_entry = _best_match_entry(
            confirm_template,
            region_ltrb=region_ltrb,
            threshold=confirm_threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
            screenshot=screenshot,
        )
        confirm_entry["key"] = "shop_confirm"
    t_confirm = _time() - t0

    rows = []
    for item_name, matches in resolved_matches.items():
        if not matches:
            continue
        match = min(matches, key=lambda current: current[1])
        row_center_y = int(_match_center_y(match))
        paired_checkbox = _pair_item_to_increment(match, checkbox_matches, y_tolerance=36)
        checkbox_target = _to_absolute_click_target(constants.GAME_WINDOW_REGION, paired_checkbox) if paired_checkbox else None
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
                "detected": True,
            }
        )

    rows = sorted(rows, key=lambda entry: entry["row_center_y"])
    visible_items = [entry["item_name"] for entry in rows]

    timing = {
        "total": round(_time() - t_total, 4),
        "templates": round(t_templates, 4),
        "families": round(t_families, 4),
        "families_resolved": families_resolved,
        "families_skipped": families_skipped,
        "checkboxes": round(t_checkboxes, 4),
        "confirm": round(t_confirm, 4),
        "rows_detected": len(rows),
        "checkbox_count": len(checkbox_matches),
    }
    info(
        f"[TB_SHOP] visible rows={len(rows)} items={visible_items} "
        f"checkboxes={len(checkbox_matches)} confirm={bool(confirm_entry and confirm_entry.get('passed_threshold'))}"
    )
    return {
        "visible_items": visible_items,
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


def scan_all_trackblazer_shop_items(
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
    }

    # Best-effort reset toward the top so the scan starts from a consistent state.
    for _ in range(max_reset_swipes):
        flow["reset_swipes"].append(scroll_trackblazer_shop(direction="up"))

    seen_items = set()
    ordered_items = []
    seen_signatures = set()
    stale_pages = 0
    last_page = None

    for page_index in range(max_forward_swipes + 1):
        page = scan_trackblazer_shop_inventory(
            threshold=threshold,
            checkbox_threshold=checkbox_threshold,
            confirm_threshold=confirm_threshold,
        )
        visible_items = list(page.get("visible_items") or [])
        signature = tuple(visible_items)
        new_items = [item_name for item_name in visible_items if item_name not in seen_items]
        for item_name in new_items:
            seen_items.add(item_name)
            ordered_items.append(item_name)

        flow["pages"].append(
            {
                "page_index": page_index,
                "visible_items": visible_items,
                "new_items": new_items,
                "rows": page.get("rows"),
                "confirm": page.get("confirm"),
                "timing": page.get("timing"),
            }
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
    return {
        "all_items": ordered_items,
        "pages": flow["pages"],
        "flow": flow,
    }


def prepare_training_items_for_use(
    item_names,
    verify_only=False,
    close_after_test=True,
    apply_confirm_use=False,
):
    """Non-destructive Trackblazer inventory test helper.

    This helper opens the Training Items inventory if needed, reuses the
    standard scan/pairing flow, increments the requested items once each, then
    verifies whether the confirm-use control reaches the available state.

    When ``apply_confirm_use`` is false, this helper never presses confirm-use.
    When ``close_after_test`` is true in that dry-run mode, it explicitly
    closes the inventory after verification to avoid consuming items.

    When ``apply_confirm_use`` is true, this helper only clicks the first
    confirm-use button as a scaffold. Any downstream confirmation screen is
    intentionally left unautomated until that flow is verified.
    """
    requested_items = [str(item_name) for item_name in (item_names or [])]
    clear_runtime_ocr_debug()

    flow = {
        "trigger": "manual_prepare_training_items",
        "execution_intent": bot.get_execution_intent(),
        "verify_only": bool(verify_only),
        "close_after_test": bool(close_after_test),
        "apply_confirm_use": bool(apply_confirm_use),
        "safety_rule": (
            "dry_run_never_presses_confirm_use"
            if not apply_confirm_use else
            "click_first_confirm_use_only_followup_confirmation_not_automated"
        ),
        "test_cleanup_reason": (
            "close inventory after verification for this test only to avoid consuming items; "
            "normal production behavior would eventually press confirm_use_available"
        ),
        "requested_items": list(requested_items),
        "opened": False,
        "already_open": False,
        "closed": False,
        "skipped": False,
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
    }
    result = {
        "requested_items": list(requested_items),
        "verify_only": bool(verify_only),
        "close_after_test": bool(close_after_test),
        "apply_confirm_use": bool(apply_confirm_use),
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

    t_total = _time()
    inventory = CleanDefaultDict()
    controls = {}

    inventory_screen_open, _, precheck_entries = detect_inventory_screen()
    flow["precheck"] = precheck_entries

    if inventory_screen_open:
        flow["opened"] = True
        flow["already_open"] = True
    else:
        info("[TB_INV_TEST] Opening Training Items inventory for non-destructive selection test.")
        t0 = _time()
        open_result = open_training_items_inventory(skip_precheck=True)
        flow["timing_open"] = round(_time() - t0, 3)
        flow["open_result"] = open_result
        flow["opened"] = bool(open_result.get("opened"))
        flow["already_open"] = bool(open_result.get("already_open"))
        if not flow["opened"]:
            flow["reason"] = "failed_to_open_inventory"
            warning("[TB_INV_TEST] Failed to open Training Items inventory.")

    try:
        if flow["opened"]:
            t0 = _time()
            inventory = scan_training_items_inventory()
            flow["timing_scan"] = round(_time() - t0, 3)
            flow["scan_timing"] = inventory.pop("_timing", None)
            result["trackblazer_inventory"] = inventory
            result["trackblazer_inventory_summary"] = build_inventory_summary(inventory)

            for item_name in requested_items:
                item_data = inventory.get(item_name) or {}
                if not item_data.get("detected"):
                    flow["missing_items"].append(item_name)
                elif not item_data.get("increment_target"):
                    flow["missing_increment_targets"].append(item_name)

            if flow["missing_items"] or flow["missing_increment_targets"]:
                missing_parts = []
                if flow["missing_items"]:
                    missing_parts.append(f"missing_items={flow['missing_items']}")
                if flow["missing_increment_targets"]:
                    missing_parts.append(f"missing_increment_targets={flow['missing_increment_targets']}")
                flow["reason"] = "required_items_not_actionable"
                warning(f"[TB_INV_TEST] Cannot continue item selection test: {' '.join(missing_parts)}")
            elif verify_only:
                flow["reason"] = "verify_only_requested"
                info("[TB_INV_TEST] verify_only=True; skipping increment clicks and checking controls only.")
            else:
                increment_t0 = _time()
                for item_name in requested_items:
                    item_data = inventory.get(item_name) or {}
                    target = item_data.get("increment_target")
                    attempt = {
                        "item_name": item_name,
                        "detected": bool(item_data.get("detected")),
                        "increment_target": list(target) if target else None,
                        "increment_match": item_data.get("increment_match"),
                        "row_center_y": item_data.get("row_center_y"),
                        "held_quantity": item_data.get("held_quantity"),
                        "click_metrics": None,
                        "clicked": False,
                    }
                    info(
                        f"[TB_INV_TEST] Incrementing '{item_name}' once at {attempt['increment_target']} "
                        "(test only, confirm-use will not be pressed)."
                    )
                    click_metrics = device_action.click_with_metrics(
                        target,
                        text=f"[TB_INV_TEST] Increment '{item_name}' once.",
                    )
                    attempt["click_metrics"] = click_metrics
                    attempt["clicked"] = bool(click_metrics.get("clicked"))
                    flow["increment_attempts"].append(attempt)
                flow["timing_increments"] = round(_time() - increment_t0, 3)

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
                info("[TB_INV_TEST] Confirm-use resolved to available/enabled after increments.")
            else:
                if not flow["reason"]:
                    flow["reason"] = "confirm_use_not_available"
                warning(
                    "[TB_INV_TEST] Confirm-use did not resolve to available after increments; "
                    "the helper will still avoid pressing it."
                )

            if apply_confirm_use and flow["confirm_use_available"]:
                confirm_target = confirm_use.get("click_target")
                info(
                    "[TB_INV_TEST] Clicking confirm-use because 'use items' is enabled. "
                    "Follow-up confirmation handling is not automated yet."
                )
                confirm_t0 = _time()
                confirm_click_result = device_action.click_with_metrics(
                    confirm_target,
                    text="[TB_INV_TEST] Click confirm-use scaffold.",
                )
                flow["timing_confirm_use"] = round(_time() - confirm_t0, 3)
                flow["confirm_use_click_result"] = confirm_click_result
                flow["confirm_use_clicked"] = bool(confirm_click_result.get("clicked"))
                post_confirm_controls_t0 = _time()
                flow["post_confirm_controls"] = detect_inventory_controls()
                flow["timing_post_confirm_controls"] = round(_time() - post_confirm_controls_t0, 3)
                if not flow["reason"] and not flow["confirm_use_clicked"]:
                    flow["reason"] = "failed_to_click_confirm_use"
            elif close_after_test:
                close_t0 = _time()
                info(
                    "[TB_INV_TEST] Closing inventory after verification for this test only to avoid consuming items. "
                    "Production item-use flow would eventually press confirm_use_available instead."
                )
                close_result = close_training_items_inventory()
                flow["timing_close"] = round(_time() - close_t0, 3)
                flow["close_result"] = close_result
                flow["closed"] = bool(close_result.get("closed"))
                if not flow["closed"] and not flow["reason"]:
                    flow["reason"] = "failed_to_close_inventory"
            elif not flow["reason"]:
                flow["reason"] = "left_inventory_open_by_request"

            increments_requested = 0 if verify_only else len(requested_items)
            flow["increments_requested"] = increments_requested
            flow["increments_clicked"] = sum(1 for attempt in flow["increment_attempts"] if attempt.get("clicked"))
            flow["success"] = (
                not flow["missing_items"]
                and not flow["missing_increment_targets"]
                and (
                    verify_only
                    or flow["increments_clicked"] == increments_requested
                )
                and flow["confirm_use_available"]
                and (
                    not apply_confirm_use
                    or flow["confirm_use_clicked"]
                )
                and (
                    apply_confirm_use
                    or not close_after_test
                    or flow["closed"]
                )
            )
        else:
            result["trackblazer_inventory_controls"] = controls
    finally:
        flow["timing_total"] = round(_time() - t_total, 3)
        result["trackblazer_inventory_flow"] = flow
        result["success"] = bool(flow.get("success"))
        record_runtime_ocr_debug(
            "trackblazer_inventory_prepare_test",
            extra={
                "region_key": "MANT_INVENTORY_ITEMS_REGION",
                "region_xywh": list(constants.MANT_INVENTORY_ITEMS_REGION),
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
        f"[TB_INV_TEST] prepare_training_items_for_use timing: total={flow.get('timing_total', '?')}s "
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

    for key in ("shop_confirm", "shop_aftersale_close"):
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


def enter_shop(threshold=0.8):
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
    t0 = _time()
    shop_coins = get_trackblazer_shop_coins() if shop_open else -1
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
    attempts = [
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

        t0 = _time()
        sleep(0.2)
        timing["sleep"] = round(_time() - t0, 4)

        t0 = _time()
        still_open, verification_entry, verification_checks = detect_shop_screen(threshold=verify_threshold)
        timing["verify"] = round(_time() - t0, 4)
        timing["total"] = round(_time() - t_total, 4)
        info(f"[TB_SHOP] close timing: {timing}")
        return {
            "closed": bool(clicked and not still_open),
            "clicked": bool(clicked),
            "attempt": entry,
            "attempts": attempt_entries,
            "verification": verification_entry,
            "verification_checks": verification_checks,
            "timing": timing,
        }

    timing["match_attempts"] = round(_time() - t0, 4)
    timing["total"] = round(_time() - t_total, 4)
    info(f"[TB_SHOP] close timing: no_match {timing}")
    return {
        "closed": False,
        "clicked": False,
        "attempt": None,
        "attempts": attempt_entries,
        "verification": None,
        "verification_checks": [],
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
            }

        dialog_template = _entry_template("shop_refresh_dialog")
        if not dialog_template:
            return {
                "method": "refresh_dialog",
                "matched": False,
                "ready": False,
                "entry": None,
                "dismiss": None,
                "best_match": None,
                "region_ltrb": [int(v) for v in _trackblazer_ui_region()],
                "required_keys": ["shop_refresh_dialog", "shop_refresh_shop", "shop_refresh_cancel"],
                "missing_required": ["shop_refresh_dialog", "shop_refresh_shop", "shop_refresh_cancel"],
                "checks": {},
                "reason": "templates_missing",
            }

        game_region = _trackblazer_ui_region()
        game_screenshot = device_action.screenshot(region_ltrb=game_region)
        dialog_entry = _best_match_entry(
            dialog_template,
            region_ltrb=game_region,
            threshold=threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
            screenshot=game_screenshot,
        )
        dialog_entry["key"] = "shop_refresh_dialog"
        checks = {"shop_refresh_dialog": dialog_entry}

        dialog_region = game_region
        if dialog_entry.get("passed_threshold") and dialog_entry.get("location") and dialog_entry.get("size"):
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

        required_keys = ("shop_refresh_dialog", "shop_refresh_shop", "shop_refresh_cancel")
        missing_required = [
            key for key in required_keys
            if not (checks.get(key) or {}).get("passed_threshold")
        ]
        scored_checks = [entry for entry in checks.values() if entry.get("score") is not None]
        best_entry = max(scored_checks, key=lambda entry: entry.get("score") or 0.0) if scored_checks else None
        matched = not missing_required
        return {
            "method": "refresh_dialog",
            "matched": matched,
            "ready": matched,
            "entry": checks.get("shop_refresh_shop"),
            "dismiss": checks.get("shop_refresh_cancel"),
            "dialog": checks.get("shop_refresh_dialog"),
            "best_match": best_entry,
            "region_ltrb": [int(v) for v in dialog_region],
            "required_keys": list(required_keys),
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

    try:
        t0 = _time()
        entry_result = enter_shop(threshold=max(0.8, threshold))
        flow["timing_open"] = round(_time() - t0, 3)
        flow["entry_result"] = entry_result
        flow["entered"] = bool(entry_result.get("entered"))
        flow["shop_coins"] = entry_result.get("shop_coins", -1)
        if not flow["entered"]:
            flow["reason"] = entry_result.get("reason") or "failed_to_enter_shop"
            return result

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
        flow["stop_reason"] = (scan_result.get("flow") or {}).get("stop_reason") or ""
        result["trackblazer_shop_items"] = list(flow["all_items"])
        result["trackblazer_shop_summary"] = {
            "items_detected": list(flow["all_items"]),
            "page_count": len(scan_result.get("pages") or []),
            "stop_reason": flow["stop_reason"],
            "shop_coins": flow["shop_coins"],
        }

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

    info(
        f"[TB_SHOP] manual shop check timing: total={flow.get('timing_total', '?')}s "
        f"open={flow.get('timing_open', '-')} scan={flow.get('timing_scan', '-')} "
        f"close={flow.get('timing_close', '-')}"
    )
    return result
