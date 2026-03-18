# scenarios/trackblazer.py
# Trackblazer (MANT) scenario-specific sub-routines.
# Inventory scanning, shop interaction, and item-use flows.

import utils.constants as constants
import utils.device_action_wrapper as device_action
import core.config as config
import core.bot as bot
from utils.log import info, warning, debug
from utils.tools import get_secs, sleep
from utils.shared import CleanDefaultDict

# Trackblazer item/shop assets are captured at the game's native screen
# resolution.  The bot applies a global template scale (currently 1.26x) to
# all templates by default, which makes these assets too large to match.
# This inverse factor cancels the global scale so they match 1:1.
_INVERSE_GLOBAL_SCALE = 1.0 / device_action.GLOBAL_TEMPLATE_SCALING


def _trackblazer_ui_region():
    return constants.GAME_WINDOW_BBOX


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
    screenshot = device_action.screenshot(region_ltrb=_trackblazer_ui_region())
    controls = {}

    close_template = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get("shop_aftersale_close")
    if close_template:
        close_entry = _best_match_entry(close_template, threshold=threshold)
        close_entry["key"] = "close"
        controls["close"] = close_entry

    confirm_candidates = {}
    for key in (
        "shop_confirm",
        "shop_aftersale_confirm_use_available",
        "shop_aftersale_confirm_use_unavailable",
    ):
        template_path = constants.TRACKBLAZER_SHOP_UI_TEMPLATES.get(key)
        if not template_path:
            continue
        entry = _best_match_entry(template_path, threshold=threshold)
        entry["key"] = key
        confirm_candidates[key] = entry

    best_confirm = None
    passed_candidates = [entry for entry in confirm_candidates.values() if entry.get("passed_threshold")]
    if passed_candidates:
        best_confirm = max(passed_candidates, key=lambda entry: entry.get("score") or 0.0)
    elif confirm_candidates:
        best_confirm = max(confirm_candidates.values(), key=lambda entry: entry.get("score") or 0.0)

    if best_confirm:
        crop = _crop_from_match(screenshot, best_confirm.get("location"), best_confirm.get("size"))
        green_ratio = _green_ratio(crop)
        best_confirm["green_ratio"] = green_ratio
        best_confirm["button_state"] = "available" if green_ratio >= 0.18 else "unavailable"
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
    region_xywh = constants.MANT_INVENTORY_ITEMS_REGION
    screenshot = device_action.screenshot(region_xywh=region_xywh)

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
    debug(
        f"[TB_INV] Increment buttons found: {len(increment_matches)} "
        f"in region {region_xywh}"
    )

    inventory = CleanDefaultDict()
    for item_name, template_path in constants.TRACKBLAZER_ITEM_TEMPLATES.items():
        matches = device_action.match_template(
            template_path, screenshot, threshold,
            template_scaling=_INVERSE_GLOBAL_SCALE,
        )
        matches = [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in matches]
        category = constants.TRACKBLAZER_ITEM_CATEGORIES.get(item_name, "unknown")

        increment_target = None
        increment_match_raw = None
        row_center_y = None
        if matches:
            # Use the first (topmost) match for pairing.
            paired = _pair_item_to_increment(matches[0], increment_matches)
            row_center_y = int(_match_center_y(matches[0]))
            if paired:
                increment_target = _to_absolute_click_target(region_xywh, paired)
                increment_target = (int(increment_target[0]), int(increment_target[1]))
                increment_match_raw = [int(v) for v in paired]

        inventory[item_name] = {
            "detected": len(matches) > 0,
            "category": category,
            "match_count": len(matches),
            "matches": [[int(v) for v in m] for m in matches],
            "increment_target": increment_target,
            "increment_match": increment_match_raw,
            "row_center_y": row_center_y,
        }
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
