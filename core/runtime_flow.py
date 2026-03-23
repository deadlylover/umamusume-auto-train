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
    "reason": "",
    "sub_phase": SUB_PHASE_IDLE,
    "popup_type": "",
    "deferred_work": [],
    "status": "idle",
    "outcome": "",
  }
