"""Shared runtime flow vocabulary for phases, sub-phases, and post-action state."""

PHASE_IDLE = "idle"
PHASE_SCANNING_LOBBY = "scanning_lobby"
PHASE_POST_ACTION_RESOLUTION = "post_action_resolution"

SUB_PHASE_IDLE = "idle"
SUB_PHASE_POST_ACTION_RESOLUTION = "post_action_resolution"
SUB_PHASE_RESOLVE_POST_ACTION_POPUP = "resolve_post_action_popup"
SUB_PHASE_RESOLVE_EVENT_CHOICE = "resolve_event_choice"
SUB_PHASE_RESOLVE_SHOP_REFRESH_POPUP = "resolve_shop_refresh_popup"
SUB_PHASE_RESOLVE_SCHEDULED_RACE_POPUP = "resolve_scheduled_race_popup"
SUB_PHASE_RESOLVE_CONSECUTIVE_RACE_WARNING = "resolve_consecutive_race_warning"
SUB_PHASE_RETURN_TO_LOBBY = "return_to_lobby"


def default_post_action_resolution_state():
  return {
    "active": False,
    "source_action": "",
    "turn_label": "",
    "reason": "",
    "sub_phase": SUB_PHASE_IDLE,
    "popup_type": "",
    "deferred_work": [],
    "status": "idle",
    "outcome": "",
    "started_at": None,
    "updated_at": None,
    "completed_at": None,
    "timing_total": None,
    "timing_event_choice": 0.0,
    "timing_screenshot_capture": 0.0,
    "timing_lobby_detect": 0.0,
    "timing_popup_checks": 0.0,
    "timing_popup_handlers": 0.0,
    "timing_followup_wait": 0.0,
    "timing_generic_recovery": 0.0,
    "timing_idle_sleep": 0.0,
    "timing_to_first_event_choice": None,
    "timing_to_stable_lobby": None,
    "timing_from_last_event_to_lobby": None,
    "timing_from_last_popup_to_lobby": None,
    "loop_count": 0,
    "event_choice_count": 0,
    "handled_popup_count": 0,
    "generic_recovery_count": 0,
    "safe_space_tap_count": 0,
    "followup_wait_count": 0,
    "idle_sleep_count": 0,
    "last_anchor_counts": {},
    "timeline_entries": [],
    "archived": False,
  }
