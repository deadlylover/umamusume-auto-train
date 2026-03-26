from pathlib import Path


def _is_bbox_name(name: str) -> bool:
  return name.endswith("_BBOX") or "_BBOX_" in name


def _is_region_name(name: str) -> bool:
  return name.endswith("_REGION") or "_REGION_" in name

def convert_xyxy_to_xywh(bbox_xyxy : tuple[int, int, int, int]) -> tuple[int, int, int, int]:
  if len(bbox_xyxy) != 4:
    raise ValueError(f"Bounding box must have 4 elements. Bounding box: {bbox_xyxy}")
  return (bbox_xyxy[0], bbox_xyxy[1], bbox_xyxy[2] - bbox_xyxy[0], bbox_xyxy[3] - bbox_xyxy[1])

def convert_xywh_to_xyxy(bbox_xywh : tuple[int, int, int, int]) -> tuple[int, int, int, int]:
  if len(bbox_xywh) != 4:
    raise ValueError(f"Bounding box must have 4 elements. Bounding box: {bbox_xywh}")
  return (bbox_xywh[0], bbox_xywh[1], bbox_xywh[0] + bbox_xywh[2], bbox_xywh[1] + bbox_xywh[3])

def add_tuple_elements(bbox, tuple_to_add):
  if len(bbox) != len(tuple_to_add) or len(tuple_to_add) != 4:
    raise ValueError(f"Bounding boxes must have the same length. Bounding box: {bbox}, Tuple to add: {tuple_to_add}")
  return (bbox[0] + tuple_to_add[0], bbox[1] + tuple_to_add[1], bbox[2] + tuple_to_add[2], bbox[3] + tuple_to_add[3])

def debug_bbox(bbox):
  print(f"Bbox: {bbox}")
  print(f"GAME_WINDOW_BBOX: {GAME_WINDOW_BBOX}")
  value_to_add = (
  bbox[0] - GAME_WINDOW_BBOX[0],
  bbox[1] - GAME_WINDOW_BBOX[1],
  (bbox[0] + bbox[2]) - GAME_WINDOW_BBOX[2],
  (bbox[1] + bbox[3]) - GAME_WINDOW_BBOX[3]
  )
  print(f"Value to add: {value_to_add}")
  result = add_tuple_elements(GAME_WINDOW_BBOX, value_to_add)
  print(f"Result: {result}")
  print(f"Result: {bbox}")

# Top left x, top left y, bottom right x, bottom right y
GAME_WINDOW_BBOX = (155, 0, 955, 1080)
# Left, top, width, height
GAME_WINDOW_REGION = convert_xyxy_to_xywh(GAME_WINDOW_BBOX)

SCREEN_TOP_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 0, 0, -780))
SCREEN_TOP_REGION = convert_xyxy_to_xywh(SCREEN_TOP_BBOX)

SCREEN_MIDDLE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 300, 0, -280))
SCREEN_MIDDLE_REGION = convert_xyxy_to_xywh(SCREEN_MIDDLE_BBOX)

SCREEN_BOTTOM_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 800, 0, 0))
SCREEN_BOTTOM_REGION = convert_xyxy_to_xywh(SCREEN_BOTTOM_BBOX)

SCROLLING_SKILL_SCREEN_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 390, 0, -200))
SCROLLING_SKILL_SCREEN_REGION = convert_xyxy_to_xywh(SCROLLING_SKILL_SCREEN_BBOX)

# Skill scrollbar — narrow vertical strip containing only the scrollbar track and thumb.
# Initial offsets are rough estimates; tune with the region adjuster on the open skills page.
SKILL_SCROLLBAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (705, 390, -18, -200))
SKILL_SCROLLBAR_REGION = convert_xyxy_to_xywh(SKILL_SCROLLBAR_BBOX)

# Skill name band — horizontal strip where skill titles appear (top portion of each card).
# Intentionally excludes the description text below the title line.
SKILL_NAME_BAND_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (50, 390, -80, -200))
SKILL_NAME_BAND_REGION = convert_xyxy_to_xywh(SKILL_NAME_BAND_BBOX)

# Skill points display — the SP counter shown near the top of the skills page.
SKILL_POINTS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (560, 85, -130, -950))
SKILL_POINTS_REGION = convert_xyxy_to_xywh(SKILL_POINTS_BBOX)

ENERGY_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (292, 120, -150, -920))
ENERGY_REGION = convert_xyxy_to_xywh(ENERGY_BBOX)

UNITY_ENERGY_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (287, 120, -150, -920))
UNITY_ENERGY_REGION = convert_xyxy_to_xywh(UNITY_ENERGY_BBOX)

# Trackblazer is the canonical scenario name. These MANT-prefixed constants are
# legacy placeholders kept for compatibility and can be tuned independently in
# the region adjuster as more Trackblazer captures are gathered.
MANT_ENERGY_BBOX = ENERGY_BBOX
MANT_ENERGY_REGION = ENERGY_REGION

MOOD_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (557, 125, -115, -930))
MOOD_REGION = convert_xyxy_to_xywh(MOOD_BBOX)

TURN_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (112, 82, -585, -947))
TURN_REGION = convert_xyxy_to_xywh(TURN_BBOX)

UNITY_TURN_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (110, 60, -630, -975))
UNITY_TURN_REGION = convert_xyxy_to_xywh(UNITY_TURN_BBOX)

MANT_TURN_BBOX = TURN_BBOX
MANT_TURN_REGION = TURN_REGION

UNITY_RACE_TURNS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (120, 114, -640, -947))
UNITY_RACE_TURNS_REGION = convert_xyxy_to_xywh(UNITY_RACE_TURNS_BBOX)

UNITY_TURN_FULL_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (110, 60, -570, -975))
UNITY_TURN_FULL_REGION = convert_xyxy_to_xywh(UNITY_TURN_FULL_BBOX)

MANT_TURN_FULL_BBOX = TURN_BBOX
MANT_TURN_FULL_REGION = TURN_REGION

FAILURE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (152, 790, -140, -260))
FAILURE_REGION = convert_xyxy_to_xywh(FAILURE_BBOX)

UNITY_FAILURE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (152, 780, -140, -265))
UNITY_FAILURE_REGION = convert_xyxy_to_xywh(UNITY_FAILURE_BBOX)

MANT_FAILURE_BBOX = FAILURE_BBOX
MANT_FAILURE_REGION = FAILURE_REGION

FAILURE_PERCENT_TEMPLATES = [
  "assets/ui/fail_percent_symbol.png",
  "assets/ui/fail_percent_orange.png",
  "assets/ui/fail_percent_red.png",
]

YEAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (107, 35, -530, -1020))
YEAR_REGION = convert_xyxy_to_xywh(YEAR_BBOX)

UNITY_YEAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (237, 35, -400, -1025))
UNITY_YEAR_REGION = convert_xyxy_to_xywh(UNITY_YEAR_BBOX)

MANT_YEAR_BBOX = YEAR_BBOX
MANT_YEAR_REGION = YEAR_REGION

CRITERIA_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (307, 60, -200, -965))
CRITERIA_REGION = convert_xyxy_to_xywh(CRITERIA_BBOX)

UNITY_CRITERIA_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (290, 60, -190, -965))
UNITY_CRITERIA_REGION = convert_xyxy_to_xywh(UNITY_CRITERIA_BBOX)

MANT_CRITERIA_BBOX = CRITERIA_BBOX
MANT_CRITERIA_REGION = CRITERIA_REGION

CURRENT_STATS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (120, 723, -122, -315))
CURRENT_STATS_REGION = convert_xyxy_to_xywh(CURRENT_STATS_BBOX)

MANT_CURRENT_STATS_BBOX = CURRENT_STATS_BBOX
MANT_CURRENT_STATS_REGION = CURRENT_STATS_REGION

MANT_LOBBY_SKILL_PTS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (650, 723, -20, -315))
MANT_LOBBY_SKILL_PTS_REGION = convert_xyxy_to_xywh(MANT_LOBBY_SKILL_PTS_BBOX)

RACE_INFO_TEXT_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (135, 335, -140, -710))
RACE_INFO_TEXT_REGION = convert_xyxy_to_xywh(RACE_INFO_TEXT_BBOX)

RACE_LIST_BOX_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (112, 580, -105, -210))
RACE_LIST_BOX_REGION = convert_xyxy_to_xywh(RACE_LIST_BOX_BBOX)

URA_STAT_GAINS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (122, 657, -110, -390))
URA_STAT_GAINS_REGION = convert_xyxy_to_xywh(URA_STAT_GAINS_BBOX)

UNITY_STAT_GAINS_2_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (122, 640, -110, -403))
UNITY_STAT_GAINS_2_REGION = convert_xyxy_to_xywh(UNITY_STAT_GAINS_2_BBOX)

UNITY_STAT_GAINS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (122, 673, -110, -378))
UNITY_STAT_GAINS_REGION = convert_xyxy_to_xywh(UNITY_STAT_GAINS_BBOX)

MANT_STAT_GAINS_BBOX = URA_STAT_GAINS_BBOX
MANT_STAT_GAINS_REGION = URA_STAT_GAINS_REGION
MANT_STAT_GAINS_2_BBOX = URA_STAT_GAINS_BBOX
MANT_STAT_GAINS_2_REGION = URA_STAT_GAINS_REGION

FULL_STATS_STATUS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (117, 575, -105, -140))
FULL_STATS_STATUS_REGION = convert_xyxy_to_xywh(FULL_STATS_STATUS_BBOX)

FULL_STATS_APTITUDE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (247, 340, -130, -640))
FULL_STATS_APTITUDE_REGION = convert_xyxy_to_xywh(FULL_STATS_APTITUDE_BBOX)

SUPPORT_CARD_ICON_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (695, 155, 0, -380))
SUPPORT_CARD_ICON_REGION = convert_xyxy_to_xywh(SUPPORT_CARD_ICON_BBOX)

UNITY_SUPPORT_CARD_ICON_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (665, 130, 0, -380))
UNITY_SUPPORT_CARD_ICON_REGION = convert_xyxy_to_xywh(UNITY_SUPPORT_CARD_ICON_BBOX)

MANT_SUPPORT_CARD_ICON_BBOX = SUPPORT_CARD_ICON_BBOX
MANT_SUPPORT_CARD_ICON_REGION = SUPPORT_CARD_ICON_REGION

MANT_SHOP_COIN_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (560, 85, -130, -950))
MANT_SHOP_COIN_REGION = convert_xyxy_to_xywh(MANT_SHOP_COIN_BBOX)

MANT_GRADE_POINT_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (150, 120, -500, -900))
MANT_GRADE_POINT_REGION = convert_xyxy_to_xywh(MANT_GRADE_POINT_BBOX)

MANT_SHOP_BUTTON_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (600, 785, -90, -190))
MANT_SHOP_BUTTON_REGION = convert_xyxy_to_xywh(MANT_SHOP_BUTTON_BBOX)
MANT_SHOP_REFRESH_DIALOG_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (100, 366, -436, -486))
MANT_SHOP_REFRESH_DIALOG_REGION = convert_xyxy_to_xywh(MANT_SHOP_REFRESH_DIALOG_BBOX)
MANT_SHOP_SCROLLSWIPE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (640, 420, -50, -260))
MANT_SHOP_SCROLLSWIPE_REGION = convert_xyxy_to_xywh(MANT_SHOP_SCROLLSWIPE_BBOX)
MANT_SHOP_SCROLLBAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (705, 280, -18, -250))
MANT_SHOP_SCROLLBAR_REGION = convert_xyxy_to_xywh(MANT_SHOP_SCROLLBAR_BBOX)
MANT_SHOP_CONTROLS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 860, 0, 0))
MANT_SHOP_CONTROLS_REGION = convert_xyxy_to_xywh(MANT_SHOP_CONTROLS_BBOX)

# Trackblazer inventory/item region — covers the item list area on the training items screen.
# Placeholder offsets; tune with the region adjuster once the screen is accessible.
MANT_INVENTORY_ITEMS_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (50, 250, -50, -200))
MANT_INVENTORY_ITEMS_REGION = convert_xyxy_to_xywh(MANT_INVENTORY_ITEMS_BBOX)
# Trackblazer inventory scrollbar — narrow strip on the right of the item list.
# Placeholder offsets; tune with the region adjuster once the screen is accessible.
MANT_INVENTORY_SCROLLBAR_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (705, 280, -18, -250))
MANT_INVENTORY_SCROLLBAR_REGION = convert_xyxy_to_xywh(MANT_INVENTORY_SCROLLBAR_BBOX)

# Trackblazer item icon templates — used for inventory and shop recognition.
TRACKBLAZER_ITEM_TEMPLATES = {
  "aroma_diffuser": "assets/trackblazer/items/aroma_diffuser.png",
  "artisan_cleat_hammer": "assets/trackblazer/items/artisan_cleat_hammer.png",
  "berry_sweet_cupcake": "assets/trackblazer/items/berry_sweet_cupcake.png",
  "coaching_megaphone": "assets/trackblazer/items/coaching_megaphone.png",
  "empowering_megaphone": "assets/trackblazer/items/empowering_megaphone.png",
  "energy_drink_max": "assets/trackblazer/items/energy_drink_max.png",
  "fluffy_pillow": "assets/trackblazer/items/fluffy_pillow.png",
  "grilled_carrots": "assets/trackblazer/items/grilled_carrots.png",
  "good_luck_charm": "assets/trackblazer/items/good_luck_charm.png",
  "guts_ankle_weights": "assets/trackblazer/items/guts_ankle_weights.png",
  "guts_notepad": "assets/trackblazer/items/guts_notepad.png",
  "guts_scroll": "assets/trackblazer/items/guts_scroll.png",
  "guts_training_application": "assets/trackblazer/items/guts_training_application.png",
  "master_cleat_hammer": "assets/trackblazer/items/master_cleat_hammer.png",
  "master_practice_guide": "assets/trackblazer/items/master_practice_guide.png",
  "miracle_cure": "assets/trackblazer/items/miracle_cure.png",
  "motivating_megaphone": "assets/trackblazer/items/motivating_megaphone.png",
  "plain_cupcake": "assets/trackblazer/items/plain_cupcake.png",
  "pocket_planner": "assets/trackblazer/items/pocket_planner.png",
  "power_ankle_weights": "assets/trackblazer/items/power_ankle_weights.png",
  "power_manual": "assets/trackblazer/items/power_manual.png",
  "power_scroll": "assets/trackblazer/items/power_scroll.png",
  "practice_drills_dvd": "assets/trackblazer/items/practice_drills_dvd.png",
  "reporters_binoculars": "assets/trackblazer/items/reporters_binoculars.png",
  "reset_whistle": "assets/trackblazer/items/reset_whistle.png",
  "royal_kale_juice": "assets/trackblazer/items/royal_kale_juice.png",
  "speed_ankle_weights": "assets/trackblazer/items/speed_ankle_weights.png",
  "speed_notepad": "assets/trackblazer/items/speed_notepad.png",
  "speed_scroll": "assets/trackblazer/items/speed_scroll.png",
  "stamina_ankle_weights": "assets/trackblazer/items/stamina_ankle_weights.png",
  "stamina_manual": "assets/trackblazer/items/stamina_manual.png",
  "vita_20": "assets/trackblazer/items/vita_20.png",
  "vita_65": "assets/trackblazer/items/vita_65.png",
  "wit_manual": "assets/trackblazer/items/wit_manual.png",
  "wit_training_application": "assets/trackblazer/items/wit_training_application.png",
  "yumy_cat_food": "assets/trackblazer/items/yumy_cat_food.png",
}

# Trackblazer item categories for decision logic.
TRACKBLAZER_ITEM_CATEGORIES = {
  "aroma_diffuser": "mood",
  "artisan_cleat_hammer": "training_boost",
  "berry_sweet_cupcake": "mood",
  "coaching_megaphone": "training_boost",
  "empowering_megaphone": "training_boost",
  "energy_drink_max": "energy",
  "fluffy_pillow": "energy",
  "grilled_carrots": "energy",
  "good_luck_charm": "training_boost",
  "guts_ankle_weights": "training_boost",
  "guts_notepad": "training_boost",
  "guts_scroll": "training_boost",
  "guts_training_application": "training_boost",
  "master_cleat_hammer": "training_boost",
  "master_practice_guide": "training_boost",
  "miracle_cure": "condition",
  "motivating_megaphone": "mood",
  "plain_cupcake": "mood",
  "pocket_planner": "training_boost",
  "power_ankle_weights": "training_boost",
  "power_manual": "training_boost",
  "power_scroll": "training_boost",
  "practice_drills_dvd": "training_boost",
  "reporters_binoculars": "training_boost",
  "reset_whistle": "condition",
  "royal_kale_juice": "energy",
  "speed_ankle_weights": "training_boost",
  "speed_notepad": "training_boost",
  "speed_scroll": "training_boost",
  "stamina_ankle_weights": "training_boost",
  "stamina_manual": "training_boost",
  "vita_20": "energy",
  "vita_65": "energy",
  "wit_manual": "training_boost",
  "wit_training_application": "training_boost",
  "yumy_cat_food": "energy",
}

# Trackblazer race-related templates (rival indicators, grade badges, warnings).
TRACKBLAZER_RACE_TEMPLATES = {
  "rival_racer": "assets/trackblazer/rival_racer.png",
  "race_recommend_2_aptitudes": "assets/trackblazer/race_recommend_2_aptitudes.png",
  "summer_rival_race_button": "assets/trackblazer/summer_rival_race_button.png",
  "rival_race_button": "assets/trackblazer/rival_race_button.png",
  "rival_race_button_vs": "assets/trackblazer/rival_race_button_VS.png",
  "race_warning_consecutive": "assets/trackblazer/race_warning_consecutive.png",
  "race_g2": "assets/trackblazer/race_g2.png",
  "race_g3": "assets/trackblazer/race_g3.png",
}

TRACKBLAZER_SHOP_UI_TEMPLATES = {
  "shop_confirm": "assets/trackblazer/shop_confirm.png",
  "shop_confirm_2": "assets/trackblazer/shop_confirm_2.png",
  "shop_aftersale_close": "assets/trackblazer/shop_aftersale_close.png",
  "shop_aftersale_confirm_use_available": "assets/trackblazer/shop_aftersale_confirm_use_available.png",
  "shop_aftersale_confirm_use_unavailable": "assets/trackblazer/shop_aftersale_confirm_use_unavailable.png",
  "inventory_confirm_use_available": "assets/trackblazer/inventory_confirm_use_available.png",
  "inventory_confirm_use_unavailable": "assets/trackblazer/inventory_confirm_use_unavailable.png",
  "shop_aftersale_confirm_use_increment_item": "assets/trackblazer/shop_aftersale_confirm_use_increment_item.png",
  "shop_item_purchased": "assets/trackblazer/shop_item_purchased.png",
  "shop_select_unchecked_grey": "assets/trackblazer/shop_select_unchecked_grey.png",
  "inventory_use_training_items": "assets/trackblazer/inventory_use_training_items.png",
  "inventory_increment_greyed": "assets/trackblazer/inventory_incremeny_greyed.png",
}

# Trackblazer lobby buff icon — visible when a megaphone or similar buff is active.
TRACKBLAZER_LOBBY_BUFF_ICON = "assets/trackblazer/lobby_buff_active.png"
# Region covering the top-left area of the game window where buff icons appear.
TRACKBLAZER_LOBBY_BUFF_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (0, 50, -670, -930))

# Trackblazer lobby "Scheduled Race" button — replaces the normal race button when a scheduled race is available.
TRACKBLAZER_LOBBY_SCHEDULED_RACE = "assets/trackblazer/lobby_scheduled_race.png"

# Trackblazer item use flow UI templates.
TRACKBLAZER_ITEM_USE_TEMPLATES = {
  "use_training_items": "assets/trackblazer/shop_use_training_items.png",
  "use_back": "assets/trackblazer/shop_use_back.png",
  "training_items_tab": "assets/trackblazer/training_items.png",
  "inventory_held": "assets/trackblazer/inventory_held.png",
  "select_checked": "assets/trackblazer/select_checked.png",
  "select_unchecked": "assets/trackblazer/select_unchecked.png",
}

# Trackblazer shop entry templates.
TRACKBLAZER_SHOP_ENTRY_TEMPLATES = {
  "shop_refresh_dialog": "assets/trackblazer/shop_refresh.png",
  "shop_sale_popup": "assets/trackblazer/shop_sale_popup.png",
  "shop_refresh_cancel": "assets/trackblazer/shop_refresh_cancel.png",
  "shop_refresh_shop": "assets/trackblazer/shop_refresh_shop.png",
  "shop_enter_lobby": "assets/buttons/shop_enter_lobby.png",
  "shop_enter_summer_lobby": "assets/trackblazer/shop_enter_summer_lobby.png",
}

UNITY_TEAM_MATCHUP_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (130, 565, -130, -475))
UNITY_TEAM_MATCHUP_REGION = convert_xyxy_to_xywh(UNITY_TEAM_MATCHUP_BBOX)

EVENT_NAME_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (92, 205, -340, -835))
EVENT_NAME_REGION = convert_xyxy_to_xywh(EVENT_NAME_BBOX)

CLAW_MACHINE_SPEED_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (690, 60, -20, -990))
CLAW_MACHINE_SPEED_REGION = convert_xyxy_to_xywh(CLAW_MACHINE_SPEED_BBOX)

CLAW_MACHINE_PLUSHIE_BBOX = add_tuple_elements(GAME_WINDOW_BBOX, (500, 450, -110, -330))
CLAW_MACHINE_PLUSHIE_REGION = convert_xyxy_to_xywh(CLAW_MACHINE_PLUSHIE_BBOX)

FULL_SCREEN_LANDSCAPE = (0, 0, 1920, 1080)

TRAINING_SWIPE_BOX_WIDTH = 28
TRAINING_SWIPE_BOX_HEIGHT = 150
TRAINING_EXECUTION_CLICK_OFFSET_Y = 20


def _training_swipe_bbox(base_x: int) -> tuple[int, int, int, int]:
  x1 = int(base_x - TRAINING_SWIPE_BOX_WIDTH // 2)
  y1 = int(GAME_WINDOW_BBOX[1] + 900)
  x2 = int(x1 + TRAINING_SWIPE_BOX_WIDTH)
  y2 = int(y1 + TRAINING_SWIPE_BOX_HEIGHT)
  return (x1, y1, x2, y2)


def _training_execution_click_pos(bbox_xyxy: tuple[int, int, int, int]) -> tuple[int, int]:
  x1, y1, x2, y2 = bbox_xyxy
  return (
    int((x1 + x2) // 2),
    int(y2 + TRAINING_EXECUTION_CLICK_OFFSET_Y),
  )


def _training_scan_drag_pos(bbox_xyxy: tuple[int, int, int, int]) -> tuple[int, int]:
  x1, y1, x2, _y2 = bbox_xyxy
  return (
    int((x1 + x2) // 2),
    int(y1),
  )


TRAINING_SWIPE_SPD_BBOX = _training_swipe_bbox(GAME_WINDOW_BBOX[0] + 185)
TRAINING_SWIPE_SPD_REGION = convert_xyxy_to_xywh(TRAINING_SWIPE_SPD_BBOX)
TRAINING_SWIPE_STA_BBOX = _training_swipe_bbox(GAME_WINDOW_BBOX[0] + 290)
TRAINING_SWIPE_STA_REGION = convert_xyxy_to_xywh(TRAINING_SWIPE_STA_BBOX)
TRAINING_SWIPE_PWR_BBOX = _training_swipe_bbox(GAME_WINDOW_BBOX[0] + 395)
TRAINING_SWIPE_PWR_REGION = convert_xyxy_to_xywh(TRAINING_SWIPE_PWR_BBOX)
TRAINING_SWIPE_GUTS_BBOX = _training_swipe_bbox(GAME_WINDOW_BBOX[0] + 500)
TRAINING_SWIPE_GUTS_REGION = convert_xyxy_to_xywh(TRAINING_SWIPE_GUTS_BBOX)
TRAINING_SWIPE_WIT_BBOX = _training_swipe_bbox(GAME_WINDOW_BBOX[0] + 605)
TRAINING_SWIPE_WIT_REGION = convert_xyxy_to_xywh(TRAINING_SWIPE_WIT_BBOX)

SCROLLING_SELECTION_MOUSE_POS=(560, 680)
SKILL_SCROLL_BOTTOM_MOUSE_POS=(560, 850)
SKILL_SCROLL_TOP_MOUSE_POS=(560, SKILL_SCROLL_BOTTOM_MOUSE_POS[1] - 300)
# TODO: Validate race list scroll positions across layouts; adjust padding if needed.
RACE_SCROLL_BOTTOM_MOUSE_POS=(0, 0)
RACE_SCROLL_TOP_MOUSE_POS=(0, 0)
MANT_SHOP_SCROLL_BOTTOM_MOUSE_POS=(0, 0)
MANT_SHOP_SCROLL_TOP_MOUSE_POS=(0, 0)

# Anchor the scan row from the same per-training swipe boxes that the OCR
# adjuster exposes, but keep execution clicks separate so the scan pass does not
# accidentally use live training-confirm targets.
SPD_SCAN_MOUSE_POS = (
  _training_scan_drag_pos(TRAINING_SWIPE_SPD_BBOX)
)
STA_SCAN_MOUSE_POS = (
  _training_scan_drag_pos(TRAINING_SWIPE_STA_BBOX)
)
PWR_SCAN_MOUSE_POS = (
  _training_scan_drag_pos(TRAINING_SWIPE_PWR_BBOX)
)
GUTS_SCAN_MOUSE_POS = (
  _training_scan_drag_pos(TRAINING_SWIPE_GUTS_BBOX)
)
WIT_SCAN_MOUSE_POS = (
  _training_scan_drag_pos(TRAINING_SWIPE_WIT_BBOX)
)

SPD_BUTTON_MOUSE_POS = (
  _training_execution_click_pos(TRAINING_SWIPE_SPD_BBOX)
)
STA_BUTTON_MOUSE_POS = (
  _training_execution_click_pos(TRAINING_SWIPE_STA_BBOX)
)
PWR_BUTTON_MOUSE_POS = (
  _training_execution_click_pos(TRAINING_SWIPE_PWR_BBOX)
)
GUTS_BUTTON_MOUSE_POS = (
  _training_execution_click_pos(TRAINING_SWIPE_GUTS_BBOX)
)
WIT_BUTTON_MOUSE_POS = (
  _training_execution_click_pos(TRAINING_SWIPE_WIT_BBOX)
)
SAFE_SPACE_MOUSE_POS = (GAME_WINDOW_BBOX[0] + 405, GAME_WINDOW_BBOX[1] + 150)

TRAINING_SCAN_POSITIONS = {
  "spd": SPD_SCAN_MOUSE_POS,
  "sta": STA_SCAN_MOUSE_POS,
  "pwr": PWR_SCAN_MOUSE_POS,
  "guts": GUTS_SCAN_MOUSE_POS,
  "wit": WIT_SCAN_MOUSE_POS
}

TRAINING_BUTTON_POSITIONS = {
  "spd": SPD_BUTTON_MOUSE_POS,
  "sta": STA_BUTTON_MOUSE_POS,
  "pwr": PWR_BUTTON_MOUSE_POS,
  "guts": GUTS_BUTTON_MOUSE_POS,
  "wit": WIT_BUTTON_MOUSE_POS
}

def name_of_variable(region_xywh):
  if region_xywh is None:
    return "None"
  else:
    # find the variable name that has the region_xywh
    for name, value in globals().items():
      if isinstance(value, tuple) and len(value) == 4 and value == region_xywh:
        return name
    return "Unknown"

def update_training_button_positions():
  global TRAINING_SCAN_POSITIONS, TRAINING_BUTTON_POSITIONS
  global SPD_SCAN_MOUSE_POS, STA_SCAN_MOUSE_POS, PWR_SCAN_MOUSE_POS
  global GUTS_SCAN_MOUSE_POS, WIT_SCAN_MOUSE_POS
  global SPD_BUTTON_MOUSE_POS, STA_BUTTON_MOUSE_POS, PWR_BUTTON_MOUSE_POS
  global GUTS_BUTTON_MOUSE_POS, WIT_BUTTON_MOUSE_POS
  global TRAINING_SWIPE_SPD_BBOX, TRAINING_SWIPE_SPD_REGION
  global TRAINING_SWIPE_STA_BBOX, TRAINING_SWIPE_STA_REGION
  global TRAINING_SWIPE_PWR_BBOX, TRAINING_SWIPE_PWR_REGION
  global TRAINING_SWIPE_GUTS_BBOX, TRAINING_SWIPE_GUTS_REGION
  global TRAINING_SWIPE_WIT_BBOX, TRAINING_SWIPE_WIT_REGION
  global SAFE_SPACE_MOUSE_POS
  training_defaults = {
    "spd": GAME_WINDOW_BBOX[0] + 185,
    "sta": GAME_WINDOW_BBOX[0] + 290,
    "pwr": GAME_WINDOW_BBOX[0] + 395,
    "guts": GAME_WINDOW_BBOX[0] + 500,
    "wit": GAME_WINDOW_BBOX[0] + 605,
  }
  swipe_bbox_names = {
    "spd": "TRAINING_SWIPE_SPD_BBOX",
    "sta": "TRAINING_SWIPE_STA_BBOX",
    "pwr": "TRAINING_SWIPE_PWR_BBOX",
    "guts": "TRAINING_SWIPE_GUTS_BBOX",
    "wit": "TRAINING_SWIPE_WIT_BBOX",
  }

  for name, base_x in training_defaults.items():
    bbox_name = swipe_bbox_names[name]
    region_name = bbox_name.replace("_BBOX", "_REGION")
    bbox_value = globals().get(bbox_name)
    if not isinstance(bbox_value, tuple) or len(bbox_value) != 4:
      bbox_value = _training_swipe_bbox(base_x)
      globals()[bbox_name] = bbox_value
      globals()[region_name] = convert_xyxy_to_xywh(bbox_value)

  SAFE_SPACE_MOUSE_POS = (GAME_WINDOW_BBOX[0] + 405, GAME_WINDOW_BBOX[1] + 150)
  SPD_SCAN_MOUSE_POS = _training_scan_drag_pos(TRAINING_SWIPE_SPD_BBOX)
  STA_SCAN_MOUSE_POS = _training_scan_drag_pos(TRAINING_SWIPE_STA_BBOX)
  PWR_SCAN_MOUSE_POS = _training_scan_drag_pos(TRAINING_SWIPE_PWR_BBOX)
  GUTS_SCAN_MOUSE_POS = _training_scan_drag_pos(TRAINING_SWIPE_GUTS_BBOX)
  WIT_SCAN_MOUSE_POS = _training_scan_drag_pos(TRAINING_SWIPE_WIT_BBOX)
  TRAINING_SCAN_POSITIONS = {
    "spd": SPD_SCAN_MOUSE_POS,
    "sta": STA_SCAN_MOUSE_POS,
    "pwr": PWR_SCAN_MOUSE_POS,
    "guts": GUTS_SCAN_MOUSE_POS,
    "wit": WIT_SCAN_MOUSE_POS
  }
  SPD_BUTTON_MOUSE_POS = _training_execution_click_pos(TRAINING_SWIPE_SPD_BBOX)
  STA_BUTTON_MOUSE_POS = _training_execution_click_pos(TRAINING_SWIPE_STA_BBOX)
  PWR_BUTTON_MOUSE_POS = _training_execution_click_pos(TRAINING_SWIPE_PWR_BBOX)
  GUTS_BUTTON_MOUSE_POS = _training_execution_click_pos(TRAINING_SWIPE_GUTS_BBOX)
  WIT_BUTTON_MOUSE_POS = _training_execution_click_pos(TRAINING_SWIPE_WIT_BBOX)
  TRAINING_BUTTON_POSITIONS = {
    "spd": SPD_BUTTON_MOUSE_POS,
    "sta": STA_BUTTON_MOUSE_POS,
    "pwr": PWR_BUTTON_MOUSE_POS,
    "guts": GUTS_BUTTON_MOUSE_POS,
    "wit": WIT_BUTTON_MOUSE_POS
  }

def update_race_scroll_positions():
  """Derive race list scroll points from the list bbox so swipes stay inside."""
  global RACE_SCROLL_BOTTOM_MOUSE_POS, RACE_SCROLL_TOP_MOUSE_POS
  x1, y1, x2, y2 = RACE_LIST_BOX_BBOX
  width = max(1, x2 - x1)
  height = max(1, y2 - y1)
  x = x1 + width // 2
  top_y = y1 + int(height * 0.25)
  bottom_y = y1 + int(height * 0.85)
  min_y = y1 + 1
  max_y = y2 - 1
  top_y = max(min_y, min(max_y, top_y))
  bottom_y = max(min_y, min(max_y, bottom_y))
  if bottom_y <= top_y:
    top_y = max(min_y, min(max_y, y1 + 1))
    bottom_y = max(min_y, min(max_y, y2 - 1))
    if bottom_y <= top_y:
      bottom_y = min(max_y, top_y + 1)
  RACE_SCROLL_TOP_MOUSE_POS = (x, top_y)
  RACE_SCROLL_BOTTOM_MOUSE_POS = (x, bottom_y)

def update_shop_scroll_positions():
  """Derive Trackblazer shop swipe points from the adjustable swipe bbox.

  Use a conservative in-box travel for ADB swipes so the shop list advances
  one page at a time without inertial overshoot at release.
  """
  global MANT_SHOP_SCROLL_BOTTOM_MOUSE_POS, MANT_SHOP_SCROLL_TOP_MOUSE_POS
  x1, y1, x2, y2 = MANT_SHOP_SCROLLSWIPE_BBOX
  width = max(1, x2 - x1)
  height = max(1, y2 - y1)
  x = x1 + width // 2
  top_y = y1 + int(height * 0.4)
  bottom_y = y1 + int(height * 0.7)
  min_y = y1 + 1
  max_y = y2 - 1
  top_y = max(min_y, min(max_y, top_y))
  bottom_y = max(min_y, min(max_y, bottom_y))
  if bottom_y <= top_y:
    top_y = max(min_y, min(max_y, y1 + 1))
    bottom_y = max(min_y, min(max_y, y2 - 1))
    if bottom_y <= top_y:
      bottom_y = min(max_y, top_y + 1)
  MANT_SHOP_SCROLL_TOP_MOUSE_POS = (x, top_y)
  MANT_SHOP_SCROLL_BOTTOM_MOUSE_POS = (x, bottom_y)

def update_action_positions():
  update_training_button_positions()
  update_race_scroll_positions()
  update_shop_scroll_positions()

update_action_positions()

SKIP_BTN_BIG_BBOX_LANDSCAPE = (1300, 750, 1920, 1080)
SKIP_BTN_BIG_REGION_LANDSCAPE = convert_xyxy_to_xywh(SKIP_BTN_BIG_BBOX_LANDSCAPE)
RACE_BUTTON_IN_RACE_BBOX_LANDSCAPE=(800, 950, 1150, 1050)
RACE_BUTTON_IN_RACE_REGION_LANDSCAPE = convert_xyxy_to_xywh(RACE_BUTTON_IN_RACE_BBOX_LANDSCAPE)
SCENARIO_NAME = ""
OFFSET_APPLIED = False
OVERRIDES_APPLIED = False
SCALE_APPLIED = False

DEFAULT_REGION_OVERRIDES_PATH = Path(__file__).resolve().parents[1] / "data" / "region_overrides.json"

LAYOUT_REGION_OFFSETS = {
  "SCREEN_TOP_BBOX": (0, 0, 0, -780),
  "SCREEN_MIDDLE_BBOX": (0, 300, 0, -280),
  "SCREEN_BOTTOM_BBOX": (0, 800, 0, 0),
  "SCROLLING_SKILL_SCREEN_BBOX": (0, 390, 0, -200),
  "MANT_SHOP_CONTROLS_BBOX": (0, 860, 0, 0),
}


def sync_layout_regions_from_game_window():
  global GAME_WINDOW_REGION
  GAME_WINDOW_REGION = convert_xyxy_to_xywh(GAME_WINDOW_BBOX)

  for bbox_name, offset in LAYOUT_REGION_OFFSETS.items():
    bbox_value = add_tuple_elements(GAME_WINDOW_BBOX, offset)
    globals()[bbox_name] = bbox_value
    globals()[bbox_name.replace("_BBOX", "_REGION")] = convert_xyxy_to_xywh(bbox_value)

  update_action_positions()

ADJUSTABLE_COORDINATE_ORDER = (
  "GAME_WINDOW_BBOX",
  "GAME_WINDOW_REGION",
  "SCREEN_TOP_BBOX",
  "SCREEN_TOP_REGION",
  "SCREEN_MIDDLE_BBOX",
  "SCREEN_MIDDLE_REGION",
  "SCREEN_BOTTOM_BBOX",
  "SCREEN_BOTTOM_REGION",
  "SCROLLING_SKILL_SCREEN_BBOX",
  "SCROLLING_SKILL_SCREEN_REGION",
  "TRAINING_SWIPE_SPD_BBOX",
  "TRAINING_SWIPE_SPD_REGION",
  "TRAINING_SWIPE_STA_BBOX",
  "TRAINING_SWIPE_STA_REGION",
  "TRAINING_SWIPE_PWR_BBOX",
  "TRAINING_SWIPE_PWR_REGION",
  "TRAINING_SWIPE_GUTS_BBOX",
  "TRAINING_SWIPE_GUTS_REGION",
  "TRAINING_SWIPE_WIT_BBOX",
  "TRAINING_SWIPE_WIT_REGION",
  "ENERGY_BBOX",
  "ENERGY_REGION",
  "UNITY_ENERGY_BBOX",
  "UNITY_ENERGY_REGION",
  "MANT_ENERGY_BBOX",
  "MANT_ENERGY_REGION",
  "MOOD_BBOX",
  "MOOD_REGION",
  "TURN_BBOX",
  "TURN_REGION",
  "UNITY_TURN_BBOX",
  "UNITY_TURN_REGION",
  "MANT_TURN_BBOX",
  "MANT_TURN_REGION",
  "UNITY_TURN_FULL_BBOX",
  "UNITY_TURN_FULL_REGION",
  "MANT_TURN_FULL_BBOX",
  "MANT_TURN_FULL_REGION",
  "UNITY_RACE_TURNS_BBOX",
  "UNITY_RACE_TURNS_REGION",
  "FAILURE_BBOX",
  "FAILURE_REGION",
  "UNITY_FAILURE_BBOX",
  "UNITY_FAILURE_REGION",
  "MANT_FAILURE_BBOX",
  "MANT_FAILURE_REGION",
  "YEAR_BBOX",
  "YEAR_REGION",
  "UNITY_YEAR_BBOX",
  "UNITY_YEAR_REGION",
  "MANT_YEAR_BBOX",
  "MANT_YEAR_REGION",
  "CRITERIA_BBOX",
  "CRITERIA_REGION",
  "UNITY_CRITERIA_BBOX",
  "UNITY_CRITERIA_REGION",
  "MANT_CRITERIA_BBOX",
  "MANT_CRITERIA_REGION",
  "CURRENT_STATS_BBOX",
  "CURRENT_STATS_REGION",
  "MANT_CURRENT_STATS_BBOX",
  "MANT_CURRENT_STATS_REGION",
  "MANT_LOBBY_SKILL_PTS_BBOX",
  "MANT_LOBBY_SKILL_PTS_REGION",
  "RACE_INFO_TEXT_BBOX",
  "RACE_INFO_TEXT_REGION",
  "RACE_LIST_BOX_BBOX",
  "RACE_LIST_BOX_REGION",
  "URA_STAT_GAINS_BBOX",
  "URA_STAT_GAINS_REGION",
  "UNITY_STAT_GAINS_BBOX",
  "UNITY_STAT_GAINS_REGION",
  "UNITY_STAT_GAINS_2_BBOX",
  "UNITY_STAT_GAINS_2_REGION",
  "MANT_STAT_GAINS_BBOX",
  "MANT_STAT_GAINS_REGION",
  "MANT_STAT_GAINS_2_BBOX",
  "MANT_STAT_GAINS_2_REGION",
  "FULL_STATS_STATUS_BBOX",
  "FULL_STATS_STATUS_REGION",
  "FULL_STATS_APTITUDE_BBOX",
  "FULL_STATS_APTITUDE_REGION",
  "SUPPORT_CARD_ICON_BBOX",
  "SUPPORT_CARD_ICON_REGION",
  "UNITY_SUPPORT_CARD_ICON_BBOX",
  "UNITY_SUPPORT_CARD_ICON_REGION",
  "MANT_SUPPORT_CARD_ICON_BBOX",
  "MANT_SUPPORT_CARD_ICON_REGION",
  "UNITY_TEAM_MATCHUP_BBOX",
  "UNITY_TEAM_MATCHUP_REGION",
  "MANT_SHOP_COIN_BBOX",
  "MANT_SHOP_COIN_REGION",
  "MANT_GRADE_POINT_BBOX",
  "MANT_GRADE_POINT_REGION",
  "MANT_SHOP_BUTTON_BBOX",
  "MANT_SHOP_BUTTON_REGION",
  "MANT_SHOP_SCROLLSWIPE_BBOX",
  "MANT_SHOP_SCROLLSWIPE_REGION",
  "MANT_SHOP_SCROLLBAR_BBOX",
  "MANT_SHOP_SCROLLBAR_REGION",
  "MANT_SHOP_CONTROLS_BBOX",
  "MANT_SHOP_CONTROLS_REGION",
  "MANT_INVENTORY_ITEMS_BBOX",
  "MANT_INVENTORY_ITEMS_REGION",
  "MANT_INVENTORY_SCROLLBAR_BBOX",
  "MANT_INVENTORY_SCROLLBAR_REGION",
  "SKILL_SCROLLBAR_BBOX",
  "SKILL_SCROLLBAR_REGION",
  "SKILL_NAME_BAND_BBOX",
  "SKILL_NAME_BAND_REGION",
  "SKILL_POINTS_BBOX",
  "SKILL_POINTS_REGION",
  "EVENT_NAME_BBOX",
  "EVENT_NAME_REGION",
  "CLAW_MACHINE_SPEED_BBOX",
  "CLAW_MACHINE_SPEED_REGION",
  "CLAW_MACHINE_PLUSHIE_BBOX",
  "CLAW_MACHINE_PLUSHIE_REGION",
  "SKIP_BTN_BIG_BBOX_LANDSCAPE",
  "SKIP_BTN_BIG_REGION_LANDSCAPE",
  "RACE_BUTTON_IN_RACE_BBOX_LANDSCAPE",
  "RACE_BUTTON_IN_RACE_REGION_LANDSCAPE",
)

ADJUSTER_TEMPLATE_MAP = {
  "GAME_WINDOW_BBOX": [
    "assets/buttons/next_btn.png",
    "assets/buttons/next2_btn.png",
    "assets/icons/event_choice_1.png",
    "assets/buttons/inspiration_btn.png",
    "assets/buttons/cancel_btn.png",
    "assets/buttons/retry_btn.png",
    "assets/ui/tazuna_hint.png",
    "assets/buttons/infirmary_btn.png",
    "assets/buttons/claw_btn.png",
    "assets/buttons/ok_2_btn.png",
    "assets/unity/unity_cup_btn.png",
    "assets/unity/unity_banner_mid_screen.png",
    "assets/buttons/close_btn.png",
  ],
  "SCREEN_BOTTOM_BBOX": [
    "assets/buttons/training_btn.png",
    "assets/buttons/rest_btn.png",
    "assets/buttons/rest_summer_btn.png",
    "assets/buttons/recreation_btn.png",
    "assets/buttons/race_day_btn.png",
    "assets/ura/ura_race_btn.png",
    "assets/buttons/race_btn.png",
    "assets/buttons/races_btn.png",
    "assets/buttons/skip_btn.png",
    "assets/buttons/skip_btn_big.png",
    "assets/buttons/view_results.png",
    "assets/buttons/confirm_btn.png",
    "assets/buttons/close_btn.png",
  ],
  "SCREEN_TOP_BBOX": [
    "assets/buttons/info_btn.png",
    "assets/buttons/details_btn.png",
    "assets/buttons/details_btn_2.png",
  ],
  "SCREEN_MIDDLE_BBOX": [
    "assets/buttons/change_btn.png",
    "assets/buttons/confirm_btn.png",
  ],
  "MANT_SHOP_CONTROLS_BBOX": [
    "assets/trackblazer/shop_confirm.png",
    "assets/trackblazer/shop_confirm_2.png",
    "assets/trackblazer/shop_aftersale_close.png",
    "assets/trackblazer/shop_aftersale_confirm_use_available.png",
    "assets/trackblazer/shop_aftersale_confirm_use_unavailable.png",
    "assets/trackblazer/inventory_confirm_use_available.png",
    "assets/trackblazer/inventory_confirm_use_unavailable.png",
  ],
}

def export_adjuster_template_map():
  base_dir = Path(__file__).resolve().parents[1]
  template_map = {}
  for region_name, templates in ADJUSTER_TEMPLATE_MAP.items():
    existing = []
    for template_path in templates:
      if (base_dir / template_path).exists():
        existing.append(template_path)
    if existing:
      template_map[region_name] = existing
  return template_map

def export_all_template_assets():
  base_dir = Path(__file__).resolve().parents[1]
  assets_dir = base_dir / "assets"
  if not assets_dir.exists():
    return []
  templates = []
  for path in assets_dir.rglob("*.png"):
    try:
      templates.append(str(path.relative_to(base_dir)))
    except ValueError:
      templates.append(str(path))
  return sorted(templates)

def export_adjustable_coordinates():
  entries = []
  seen = set()

  def _add_entry(name, value):
    kind = "bbox" if _is_bbox_name(name) else "region"
    entries.append(
      {
        "name": name,
        "kind": kind,
        "value": [int(round(v)) for v in value],
      }
    )
    seen.add(name)

  for name in ADJUSTABLE_COORDINATE_ORDER:
    value = globals().get(name)
    if isinstance(value, tuple) and len(value) == 4:
      _add_entry(name, value)

  for name, value in sorted(globals().items()):
    if name in seen:
      continue
    if not (_is_region_name(name) or _is_bbox_name(name)):
      continue
    if not isinstance(value, tuple) or len(value) != 4:
      continue
    _add_entry(name, value)

  return entries

def apply_region_overrides(overrides_path=None, force=False):
  global OVERRIDES_APPLIED
  if OVERRIDES_APPLIED and not force:
    return False

  overrides_file = Path(overrides_path) if overrides_path else DEFAULT_REGION_OVERRIDES_PATH
  if not overrides_file.exists():
    return False

  try:
    with overrides_file.open("r", encoding="utf-8") as file:
      overrides = json.load(file)
  except Exception:
    return False

  if not isinstance(overrides, dict):
    return False

  g = globals()
  for name, value in overrides.items():
    if name not in g:
      continue
    if not (_is_region_name(name) or _is_bbox_name(name)):
      continue
    if not isinstance(value, (list, tuple)) or len(value) < 4:
      continue
    g[name] = tuple(int(round(v)) for v in value[:4])

  sync_layout_regions_from_game_window()
  update_action_positions()
  OVERRIDES_APPLIED = True
  return True


def scale_coordinate_constants(scale=1.0):
  """Scale all coordinate constants from the top-left origin.

  This is intended for macOS display-aware calibration, where saved OCR
  overrides were captured on one desktop size and need to be projected onto a
  different current desktop size.
  """
  global SCALE_APPLIED
  if SCALE_APPLIED:
    return

  try:
    scale = float(scale)
  except (TypeError, ValueError):
    scale = 1.0

  if scale <= 0 or abs(scale - 1.0) < 1e-6:
    return

  g = globals()
  for name, value in list(g.items()):
    if _is_region_name(name) and isinstance(value, tuple) and len(value) == 4:
      x, y, w, h = value
      g[name] = (
        int(round(x * scale)),
        int(round(y * scale)),
        max(1, int(round(w * scale))),
        max(1, int(round(h * scale))),
      )

    if (
      name.endswith("_MOUSE_POS")
      and isinstance(value, tuple)
      and len(value) == 2
    ):
      x, y = value
      g[name] = (
        int(round(x * scale)),
        int(round(y * scale)),
      )

    if _is_bbox_name(name) and isinstance(value, tuple) and len(value) == 4:
      x1, y1, x2, y2 = value
      scaled_x1 = int(round(x1 * scale))
      scaled_y1 = int(round(y1 * scale))
      scaled_x2 = int(round(x2 * scale))
      scaled_y2 = int(round(y2 * scale))
      if scaled_x2 <= scaled_x1:
        scaled_x2 = scaled_x1 + 1
      if scaled_y2 <= scaled_y1:
        scaled_y2 = scaled_y1 + 1
      g[name] = (
        scaled_x1,
        scaled_y1,
        scaled_x2,
        scaled_y2,
      )

  update_action_positions()
  SCALE_APPLIED = True

def adjust_constants_x_coords(offset=405):
  """Shift all region tuples' x-coordinates by `offset`."""

  global OFFSET_APPLIED
  if OFFSET_APPLIED:
    return

  g = globals()
  for name, value in list(g.items()):
    if _is_region_name(name) and isinstance(value, tuple) and len(value) == 4:
      new_value = (
        value[0] + offset,
        value[1],
        value[2],
        value[3],
      )
      g[name] = tuple(x for x in new_value if x is not None)

    if (
      name.endswith("_MOUSE_POS")
      and isinstance(value, tuple)
      and len(value) == 2
    ):
      new_value = (
        value[0] + offset,
        value[1],
      )
      g[name] = tuple(x for x in new_value if x is not None)

    if _is_bbox_name(name) and isinstance(value, tuple) and len(value) == 4:
      new_value = (
        value[0] + offset,
        value[1],
        value[2] + offset,
        value[3],
      )
      g[name] = tuple(x for x in new_value if x is not None)

  update_action_positions()
  OFFSET_APPLIED = True

def adjust_constants_offsets(x_offset=0, y_offset=0):
  """Shift all region tuples' coordinates by x and y offsets (macOS support)."""
  global OFFSET_APPLIED
  if OFFSET_APPLIED:
    return

  g = globals()
  for name, value in list(g.items()):
    if _is_region_name(name) and isinstance(value, tuple) and len(value) == 4:
      new_value = (
        value[0] + x_offset,
        value[1] + y_offset,
        value[2],
        value[3],
      )
      g[name] = new_value

    if (
      name.endswith("_MOUSE_POS")
      and isinstance(value, tuple)
      and len(value) == 2
    ):
      new_value = (
        value[0] + x_offset,
        value[1] + y_offset,
      )
      g[name] = new_value

    if _is_bbox_name(name) and isinstance(value, tuple) and len(value) == 4:
      new_value = (
        value[0] + x_offset,
        value[1] + y_offset,
        value[2] + x_offset,
        value[3] + y_offset,
      )
      g[name] = new_value

  update_training_button_positions()
  OFFSET_APPLIED = True



# Track recognition offsets separately for OCR regions
RECOGNITION_OFFSET_X = 0
RECOGNITION_OFFSET_Y = 0

def apply_recognition_offsets(x_offset=0, y_offset=0):
  """Store recognition offsets for OCR calibration (macOS support)."""
  global RECOGNITION_OFFSET_X, RECOGNITION_OFFSET_Y
  RECOGNITION_OFFSET_X = x_offset
  RECOGNITION_OFFSET_Y = y_offset

def extract_unique_letters(array):
  upper = set()
  lower = set()
  other = set()

  for s in array:
    for c in s:
      if c.isupper():
        upper.add(c)
      elif c.islower():
        lower.add(c)
      else:
        other.add(c)

  return (
    "".join(sorted(lower)) +
    "".join(sorted(upper)) +
    "".join(sorted(other, reverse=True))
  )

TIMELINE = [
  "Junior Year Pre-Debut",
  "Junior Year Early Jun",
  "Junior Year Late Jun",
  "Junior Year Early Jul",
  "Junior Year Late Jul",
  "Junior Year Early Aug",
  "Junior Year Late Aug",
  "Junior Year Early Sep",
  "Junior Year Late Sep",
  "Junior Year Early Oct",
  "Junior Year Late Oct",
  "Junior Year Early Nov",
  "Junior Year Late Nov",
  "Junior Year Early Dec",
  "Junior Year Late Dec",
  "Classic Year Early Jan",
  "Classic Year Late Jan",
  "Classic Year Early Feb",
  "Classic Year Late Feb",
  "Classic Year Early Mar",
  "Classic Year Late Mar",
  "Classic Year Early Apr",
  "Classic Year Late Apr",
  "Classic Year Early May",
  "Classic Year Late May",
  "Classic Year Early Jun",
  "Classic Year Late Jun",
  "Classic Year Early Jul",
  "Classic Year Late Jul",
  "Classic Year Early Aug",
  "Classic Year Late Aug",
  "Classic Year Early Sep",
  "Classic Year Late Sep",
  "Classic Year Early Oct",
  "Classic Year Late Oct",
  "Classic Year Early Nov",
  "Classic Year Late Nov",
  "Classic Year Early Dec",
  "Classic Year Late Dec",
  "Senior Year Early Jan",
  "Senior Year Late Jan",
  "Senior Year Early Feb",
  "Senior Year Late Feb",
  "Senior Year Early Mar",
  "Senior Year Late Mar",
  "Senior Year Early Apr",
  "Senior Year Late Apr",
  "Senior Year Early May",
  "Senior Year Late May",
  "Senior Year Early Jun",
  "Senior Year Late Jun",
  "Senior Year Early Jul",
  "Senior Year Late Jul",
  "Senior Year Early Aug",
  "Senior Year Late Aug",
  "Senior Year Early Sep",
  "Senior Year Late Sep",
  "Senior Year Early Oct",
  "Senior Year Late Oct",
  "Senior Year Early Nov",
  "Senior Year Late Nov",
  "Senior Year Early Dec",
  "Senior Year Late Dec",
  "Finale Underway",
]

OCR_DATE_RECOGNITION_SET = extract_unique_letters(TIMELINE)

TRAINING_IMAGES = {
  "spd": "assets/icons/train_spd.png",
  "sta": "assets/icons/train_sta.png",
  "pwr": "assets/icons/train_pwr.png",
  "guts": "assets/icons/train_guts.png",
  "wit": "assets/icons/train_wit.png"
}

SUPPORT_ICONS = {
  "spd": "assets/icons/support_card_type_spd.png",
  "sta": "assets/icons/support_card_type_sta.png",
  "pwr": "assets/icons/support_card_type_pwr.png",
  "guts": "assets/icons/support_card_type_guts.png",
  "wit": "assets/icons/support_card_type_wit.png",
  "friend": "assets/icons/support_card_type_friend.png"
}

SUPPORT_FRIEND_LEVELS = {
  "gray": [110,108,120],
  "blue": [42,192,255],
  "green": [162,230,30],
  "yellow": [255,173,30],
  "max": [255,235,120],
}

APTITUDE_IMAGES = {
  "a" : "assets/ui/aptitude_a.png",
  "g" : "assets/ui/aptitude_g.png",
  "b" : "assets/ui/aptitude_b.png",
  "c" : "assets/ui/aptitude_c.png",
  "d" : "assets/ui/aptitude_d.png",
  "e" : "assets/ui/aptitude_e.png",
  "f" : "assets/ui/aptitude_f.png",
  "s" : "assets/ui/aptitude_s.png"
}

MOOD_IMAGES = {
  "GREAT" : "assets/icons/mood_great.png",
  "GOOD" : "assets/icons/mood_good.png",
  "NORMAL" : "assets/icons/mood_normal.png",
  "BAD" : "assets/icons/mood_bad.png",
  "AWFUL" : "assets/icons/mood_awful.png"
}

MOOD_LIST = ["AWFUL", "BAD", "NORMAL", "GOOD", "GREAT", "UNKNOWN"]

# Load races data
import json
import os

_races_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "races.json")
with open(_races_path, 'r', encoding='utf-8') as f:
  _races_raw = json.load(f)

# Transform races to match state year format (e.g., "Junior Year Early Dec")
RACES = {}
for full_year_key in TIMELINE:
  RACES[full_year_key] = []

for year_category, races in _races_raw.items():
  for race_name, race_data in races.items():

    full_year_key = f"{year_category} {race_data['date']}"
    race_entry = {"name": race_name}
    race_entry.update(race_data)
    RACES[full_year_key].append(race_entry)

import copy
ALL_RACES = copy.deepcopy(RACES)

# Severity -> 0 is doesn't matter / incurable, 1 is "can be ignored for a few turns", 2 is "must be cured immediately"
BAD_STATUS_EFFECTS={
  "Migraine":{
    "Severity":1,
    "Effect":"Mood cannot be increased",
  },
  "Night Owl":{
    "Severity":1,
    "Effect":"Character may lose energy, and possibly mood",
  },
  "Practice Poor":{
    "Severity":1,
    "Effect":"Increases chance of training failure by 2%",
  },
  "Skin Outbreak":{
    "Severity":1,
    "Effect":"Character's mood may decrease by one stage.",
  },
  "Slacker":{
    "Severity":2,
    "Effect":"Character may not show up for training.",
  },
  "Slow Metabolism":{
    "Severity":1,
    "Effect":"Character cannot gain Speed from speed training.",
  },
  "Under the Weather":{
    "Severity":0,
    "Effect":"Increases chance of training failure by 5%"
  },
}

GOOD_STATUS_EFFECTS={
  "Charming":"Raises Friendship Bond gain by 2",
  "Fast Learner":"Reduces the cost of skills by 10%",
  "Hot Topic":"Raises Friendship Bond gain for NPCs by 2",
  "Practice Perfect":"Lowers chance of training failure by 2%",
  "Shining Brightly":"Lowers chance of training failure by 5%"
}
