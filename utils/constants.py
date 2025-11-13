import json
from pathlib import Path

MOOD_REGION=(705, 125, 835 - 705, 150 - 125)
TURN_REGION=(260, 81, 370 - 260, 140 - 87)
FAILURE_REGION=(250, 770, 855 - 295, 835 - 770)
YEAR_REGION=(255, 35, 420 - 255, 60 - 35)
CRITERIA_REGION=(455, 55, 765 - 455, 115 - 55)
EVENT_NAME_REGION=(241, 205, 365, 30)
RECREATION_REGION=(275, 300, 620, 300) # RECREATION_REGION=(0, 400, 620, 300) original
SKILL_PTS_REGION=(760, 780, 825 - 760, 815 - 780)
# Original landscape references (retain for Windows preset)
# SKIP_BTN_BIG_REGION_LANDSCAPE=(1500, 750, 1920-1500, 1080-750)
# SCREEN_BOTTOM_REGION=(125, 800, 1000-125, 1080-800)
# SCREEN_MIDDLE_REGION=(125, 300, 1000-125, 800-300)
# SCREEN_TOP_REGION=(125, 0, 1000-125, 300)

# Portrait (Bluestacks Air on macOS) layout adjustments
SKIP_BTN_BIG_REGION_LANDSCAPE=(450, 750, 620-450, 1080-750)
SCREEN_BOTTOM_REGION=(0, 750, 620, 330)
SCREEN_MIDDLE_REGION=(0, 300, 620, 450)
SCREEN_TOP_REGION=(0, 0, 620, 300)
RACE_INFO_TEXT_REGION=(285, 335, 810-285, 370-335)
RACE_LIST_BOX_REGION=(260, 580, 850-265, 870-580)

FULL_STATS_STATUS_REGION=(265, 575, 845-265, 940-575)
FULL_STATS_APTITUDE_REGION=(395, 340, 820-395, 440-340)

SCROLLING_SELECTION_MOUSE_POS=(560, 680)
SKILL_SCROLL_BOTTOM_MOUSE_POS=(560, 850)
RACE_SCROLL_BOTTOM_MOUSE_POS=(560, 850)

SPD_STAT_REGION = (310, 723, 55, 20)
STA_STAT_REGION = (405, 723, 55, 20)
PWR_STAT_REGION = (500, 723, 55, 20)
GUTS_STAT_REGION = (595, 723, 55, 20)
WIT_STAT_REGION = (690, 723, 55, 20)

MOOD_LIST = ["AWFUL", "BAD", "NORMAL", "GOOD", "GREAT", "UNKNOWN"]

SUPPORT_CARD_ICON_BBOX=(845, 140, 945, 700)
ENERGY_BBOX=(440, 120, 800, 160)
RACE_BUTTON_IN_RACE_BBOX_LANDSCAPE=(800, 950, 1150, 1050)

GAME_SCREEN_REGION = (0, 50, 620, 1080)

OCR_REGION_NAMES = {
  "MOOD_REGION",
  "TURN_REGION",
  "FAILURE_REGION",
  "YEAR_REGION",
  "CRITERIA_REGION",
  "EVENT_NAME_REGION",
  "RECREATION_REGION",
  "SKILL_PTS_REGION",
  "RACE_INFO_TEXT_REGION",
  "RACE_LIST_BOX_REGION",
  "FULL_STATS_STATUS_REGION",
  "FULL_STATS_APTITUDE_REGION",
  "SPD_STAT_REGION",
  "STA_STAT_REGION",
  "PWR_STAT_REGION",
  "GUTS_STAT_REGION",
  "WIT_STAT_REGION",
}

OCR_BBOX_NAMES = {
  "SUPPORT_CARD_ICON_BBOX",
  "ENERGY_BBOX",
}

ADJUSTABLE_EXTRA_REGION_NAMES = {
  "SCREEN_BOTTOM_REGION",
  "SCREEN_MIDDLE_REGION",
  "SCREEN_TOP_REGION",
  "GAME_SCREEN_REGION",
}

ADJUSTABLE_COORDINATE_NAMES = tuple(sorted(OCR_REGION_NAMES | OCR_BBOX_NAMES | ADJUSTABLE_EXTRA_REGION_NAMES))

GENERAL_OFFSET_APPLIED = False
RECOGNITION_OFFSET_APPLIED = False
_COORD_SNAPSHOT = {}
REGION_OVERRIDES_ACTIVE = False


def _capture_original_coordinates():
  g = globals()
  targets = ("_REGION", "_BBOX", "_MOUSE_POS")
  for name, value in list(g.items()):
    if not any(name.endswith(suffix) for suffix in targets):
      continue
    if isinstance(value, tuple):
      _COORD_SNAPSHOT[name] = tuple(value)


_capture_original_coordinates()

def _shift_constants(
  x_offset=0,
  y_offset=0,
  *,
  include_regions=True,
  include_bboxes=True,
  include_mouse=True,
  allowed_regions=None,
  allowed_bboxes=None,
  allowed_mouse=None,
):
  if x_offset == 0 and y_offset == 0:
    return

  g = globals()
  for name, value in list(g.items()):
    if include_regions and name.endswith("_REGION") and isinstance(value, tuple) and len(value) >= 4:
      if allowed_regions is not None and name not in allowed_regions:
        continue
      g[name] = (
        value[0] + x_offset,
        value[1] + y_offset,
        value[2],
        value[3],
      )

    if include_mouse and name.endswith("_MOUSE_POS") and isinstance(value, tuple) and len(value) >= 2:
      if allowed_mouse is not None and name not in allowed_mouse:
        continue
      g[name] = (
        value[0] + x_offset,
        value[1] + y_offset,
      )

    if include_bboxes and name.endswith("_BBOX") and isinstance(value, tuple) and len(value) >= 4:
      if allowed_bboxes is not None and name not in allowed_bboxes:
        continue
      g[name] = (
        value[0] + x_offset,
        value[1] + y_offset,
        value[2] + x_offset,
        value[3] + y_offset,
      )


def adjust_constants_offsets(x_offset=0, y_offset=0):
  """Shift regions, bboxes, and mouse positions. Used for legacy/general offsets."""

  global GENERAL_OFFSET_APPLIED
  if GENERAL_OFFSET_APPLIED:
    return

  _shift_constants(x_offset, y_offset, include_regions=True, include_bboxes=True, include_mouse=True)
  GENERAL_OFFSET_APPLIED = True


def apply_recognition_offsets(x_offset=0, y_offset=0):
  """Shift only regions/bboxes for recognition-specific tuning."""

  global RECOGNITION_OFFSET_APPLIED
  if RECOGNITION_OFFSET_APPLIED:
    return

  _shift_constants(
    x_offset,
    y_offset,
    include_regions=True,
    include_bboxes=True,
    include_mouse=False,
    allowed_regions=OCR_REGION_NAMES,
    allowed_bboxes=OCR_BBOX_NAMES,
  )
  RECOGNITION_OFFSET_APPLIED = True


def adjust_constants_x_coords(offset=405):
  """Backward-compatible wrapper for existing code paths."""
  adjust_constants_offsets(x_offset=offset, y_offset=0)


def reset_coordinate_constants():
  """Restore REGION/BBOX/MOUSE tuples to their original values and clear offset flags."""

  global GENERAL_OFFSET_APPLIED, RECOGNITION_OFFSET_APPLIED, REGION_OVERRIDES_ACTIVE
  if not _COORD_SNAPSHOT:
    return

  g = globals()
  for name, value in _COORD_SNAPSHOT.items():
    g[name] = tuple(value)

  GENERAL_OFFSET_APPLIED = False
  RECOGNITION_OFFSET_APPLIED = False
  # Region overrides piggy-back on the same coordinate globals, so clearing the
  # snapshot restores the factory defaults as well.
  REGION_OVERRIDES_ACTIVE = False


def adjustable_coordinate_names():
  return ADJUSTABLE_COORDINATE_NAMES


def _is_region_name(name):
  return name in OCR_REGION_NAMES or name in ADJUSTABLE_EXTRA_REGION_NAMES


def _is_bbox_name(name):
  return name in OCR_BBOX_NAMES


def export_adjustable_coordinates():
  """Return the current OCR regions/bboxes for external tooling."""

  payload = []
  g = globals()
  for name in ADJUSTABLE_COORDINATE_NAMES:
    value = g.get(name)
    if not isinstance(value, tuple):
      continue
    kind = "region" if _is_region_name(name) else "bbox"
    payload.append({"name": name, "kind": kind, "value": list(value)})
  return payload


def _normalize_override_tuple(name, value):
  if not isinstance(value, (list, tuple)):
    raise ValueError(f"Override for {name} must be a list/tuple, got {type(value).__name__}.")

  if len(value) < 4:
    raise ValueError(f"Override for {name} must have four elements, got {len(value)}.")

  normalized = tuple(int(round(v)) for v in value[:4])
  if _is_region_name(name):
    # Regions are stored as (x, y, width, height).
    return normalized

  if _is_bbox_name(name):
    # Bounding boxes use (x1, y1, x2, y2) to align with template-matching
    # expectations.
    x1, y1, x2, y2 = normalized
    if x2 <= x1 or y2 <= y1:
      raise ValueError(f"Override for {name} produces invalid bbox coordinates: {normalized}.")
    return normalized

  raise ValueError(f"{name} is not a recognized OCR region/bbox.")


def apply_region_overrides(overrides):
  """Apply overrides from an in-memory mapping."""

  if not overrides:
    return False

  global REGION_OVERRIDES_ACTIVE
  changed = False
  g = globals()

  for name, value in overrides.items():
    if name not in ADJUSTABLE_COORDINATE_NAMES:
      continue
    try:
      normalized = _normalize_override_tuple(name, value)
    except ValueError:
      continue
    g[name] = normalized
    changed = True

  if changed:
    REGION_OVERRIDES_ACTIVE = True
  return changed


def apply_region_overrides_from_path(path):
  """Load overrides from JSON (name -> tuple) and apply them."""

  if not path:
    return False

  target_path = Path(path)
  if not target_path.exists():
    return False

  try:
    with target_path.open("r", encoding="utf-8") as file:
      data = json.load(file)
  except Exception:
    return False

  if isinstance(data, dict):
    return apply_region_overrides(data)

  return False

# Load all races once to be used when selecting them
from pathlib import Path

RACES = ""
_DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "races.json"
try:
  with _DATA_PATH.open("r", encoding="utf-8") as file:
    RACES = json.load(file)
except FileNotFoundError:
  raise FileNotFoundError(f"Missing races JSON at {_DATA_PATH}") from None

# Build a lookup dict for fast (year, date) searches
RACE_LOOKUP = {}
for year, races in RACES.items():
  for name, data in races.items():
    key = f"{year} {data['date']}"
    race_entry = {"name": name, **data}
    RACE_LOOKUP.setdefault(key, []).append(race_entry)
