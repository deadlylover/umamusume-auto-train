# scenarios/trackblazer.py
# Trackblazer (MANT) scenario-specific sub-routines.
# Inventory scanning, shop interaction, and item-use flows.

import utils.constants as constants
import utils.device_action_wrapper as device_action
import core.config as config
import core.bot as bot
from core.ocr import extract_text
from utils.log import info, warning, debug
from utils.tools import get_secs, sleep
from utils.screenshot import enhance_image_for_ocr
from utils.shared import CleanDefaultDict
from PIL import Image
from time import time as _time
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


def _best_match_entry(template_path, region_ltrb=None, threshold=0.8, template_scaling=_INVERSE_GLOBAL_SCALE):
    """Return a single best-match payload for a Trackblazer UI template."""
    region_ltrb = region_ltrb or _trackblazer_ui_region()
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
        close_entry = _best_match_entry(close_template, region_ltrb=controls_region, threshold=threshold)
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
        )
        entry["key"] = key
        inventory_state_entries[key] = entry

    confirm_candidates = {}
    for key in ("shop_confirm", "shop_aftersale_confirm_use_available", "shop_aftersale_confirm_use_unavailable"):
        template_path = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get(key)
        if not template_path:
            continue
        entry_threshold = _CONFIRM_USE_SCORE_THRESHOLD if "confirm_use" in key else threshold
        entry = _best_match_entry(template_path, region_ltrb=controls_region, threshold=entry_threshold)
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
    matches = device_action.match_template(
        template_path,
        screenshot,
        threshold,
        template_scaling=_INVERSE_GLOBAL_SCALE,
    )
    if not matches:
        return None

    best_match = max(matches, key=lambda match: (match[1] + match[3] // 2, match[0] + match[2] // 2))
    x, y, w, h = best_match
    return {
        "template": template_path,
        "threshold": threshold,
        "match": [int(x), int(y), int(w), int(h)],
        "click_target": (
            int(region_ltrb[0] + x + w // 2),
            int(region_ltrb[1] + y + h // 2),
        ),
    }


def detect_inventory_screen(threshold=0.8):
    """Check whether the Trackblazer inventory/item-use screen is open."""
    checks = []
    for key in ("use_training_items", "use_back"):
        template_path = constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get(key)
        if not template_path:
            continue
        entry = _best_match_entry(template_path, threshold=threshold)
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


def open_training_items_inventory(threshold=0.8, verify_threshold=0.8):
    """Open the Trackblazer inventory screen from the lobby button."""
    already_open, verified_entry, checks = detect_inventory_screen(threshold=verify_threshold)
    if already_open:
        return {
            "opened": True,
            "already_open": True,
            "clicked": False,
            "button": None,
            "verification": verified_entry,
            "verification_checks": checks,
        }

    button = detect_training_items_button(threshold=threshold)
    if not button:
        return {
            "opened": False,
            "already_open": False,
            "clicked": False,
            "button": None,
            "verification": None,
            "verification_checks": checks,
        }

    clicked = device_action.click(button["click_target"])
    sleep(0.6)
    opened, verified_entry, verify_checks = detect_inventory_screen(threshold=verify_threshold)
    return {
        "opened": bool(clicked and opened),
        "already_open": False,
        "clicked": bool(clicked),
        "button": button,
        "verification": verified_entry,
        "verification_checks": verify_checks,
    }


def close_training_items_inventory(threshold=0.8):
    """Close the Trackblazer inventory screen with Trackblazer-first fallbacks."""
    attempts = [
        ("use_back", constants.TRACKBLAZER_ITEM_USE_TEMPLATES.get("use_back"), _INVERSE_GLOBAL_SCALE),
        ("close_btn", "assets/buttons/close_btn.png", 1.0),
        ("back_btn", "assets/buttons/back_btn.png", 1.0),
    ]
    attempt_entries = []
    for key, template_path, template_scaling in attempts:
        if not template_path:
            continue
        entry = _best_match_entry(
            template_path,
            threshold=threshold,
            template_scaling=template_scaling,
        )
        entry["key"] = key
        attempt_entries.append(entry)
        if not entry["passed_threshold"]:
            continue
        clicked = device_action.click(entry["click_target"])
        sleep(0.5)
        still_open, _, checks = detect_inventory_screen(threshold=threshold)
        return {
            "closed": bool(clicked and not still_open),
            "clicked": bool(clicked),
            "attempt": entry,
            "attempts": attempt_entries,
            "verification_checks": checks,
        }
    return {
        "closed": False,
        "clicked": False,
        "attempt": None,
        "attempts": attempt_entries,
        "verification_checks": [],
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
            # Skip expensive re-match when only one family member is in the cluster.
            unique_names = set(entry["item_name"] for entry in cluster)
            if len(unique_names) == 1:
                entry = max(cluster, key=lambda e: e.get("score", 0) if "score" in e else 0)
                resolved_matches[entry["item_name"]].append(entry["match"])
                families_skipped += 1
                continue
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
            "increment_target": increment_target,
            "increment_match": increment_match_raw,
            "row_center_y": row_center_y,
            "held_quantity": held_quantity,
            "remaining_quantity": remaining_quantity,
            "quantity_text": quantity_text,
            "quantity_region": quantity_region,
        }

    t_total_elapsed = _time() - t_total
    info(
        f"[TB_INV] scan timing: total={t_total_elapsed:.2f}s "
        f"held_ocr={t_held:.2f}s templates={t_templates:.2f}s "
        f"families={t_families:.2f}s (resolved={families_resolved} skipped={families_skipped}) "
        f"items_detected={len(detected_items_for_pairing)} quantity_reads={len(held_quantities)}"
    )
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
    """Check if a shop entry button is visible on the lobby screen.

    Checks both the lobby shop button and the shop-refresh popup button.
    Returns (button_key, match_location) or (None, None).
    """
    for key, template in constants.TRACKBLAZER_SHOP_ENTRY_TEMPLATES.items():
        match = device_action.locate(template, min_search_time=get_secs(0.5))
        if match:
            return key, match
    return None, None


def inspect_shop_entry_state(threshold=0.8):
    """Collect a debug-friendly summary of Trackblazer shop entry detection."""
    checks = {}
    for key, template_path in constants.TRACKBLAZER_SHOP_ENTRY_TEMPLATES.items():
        entry = _best_match_entry(
            template_path,
            threshold=threshold,
            template_scaling=1.0,
        )
        entry["key"] = key
        checks[key] = entry

    passed = [entry for entry in checks.values() if entry.get("passed_threshold")]
    best_entry = None
    if passed:
        best_entry = max(passed, key=lambda entry: entry.get("score") or 0.0)
    elif checks:
        best_entry = max(checks.values(), key=lambda entry: entry.get("score") or 0.0)

    return {
        "threshold": threshold,
        "matched": bool(best_entry and best_entry.get("passed_threshold")),
        "button_key": best_entry.get("key") if best_entry else None,
        "best_match": best_entry,
        "checks": checks,
    }
