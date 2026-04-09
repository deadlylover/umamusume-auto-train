import copy
from contextlib import ExitStack
from unittest.mock import patch

import core.bot as bot
import core.config as config
import utils.constants as constants
from core.actions import Action
from core.skeleton import _activate_trackblazer_planner_turn, build_review_snapshot


def _base_state():
  return {
    "year": "Senior Year Early Sep",
    "turn": 9,
    "criteria": "Reach the next fan target",
    "energy_level": 45,
    "max_energy": 100,
    "current_mood": "GOOD",
    "current_stats": {"spd": 500, "sta": 420, "pwr": 410, "guts": 240, "wit": 330, "sp": 180},
    "date_event_available": True,
    "race_mission_available": False,
    "aptitudes": {"track": "A", "distance": "A"},
    "status_effect_names": [],
    "rival_indicator_detected": False,
    "trackblazer_climax": False,
    "trackblazer_climax_locked_race": False,
    "trackblazer_climax_race_day": False,
    "trackblazer_lobby_scheduled_race": False,
    "trackblazer_trainings_remaining_upper_bound": 6,
    "trackblazer_inventory_summary": {"held_quantities": {}, "items_detected": [], "actionable_items": [], "by_category": {}, "total_detected": 0},
    "trackblazer_inventory_flow": {},
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {},
    "skill_purchase_check": {"should_check": False, "current_sp": 120, "threshold_sp": 400, "auto_buy_skill_enabled": False, "reason": "below threshold"},
    "skill_purchase_flow": {"skipped": True, "reason": "below threshold"},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "training_results": {
      "speed": {
        "name": "speed",
        "score_tuple": (24.0, 0),
      "weighted_stat_score": 24.0,
      "stat_gains": {"speed": 16, "power": 4, "sp": 8},
      "failure": 3,
      "total_supports": 3,
      "total_rainbow_friends": 1,
      "total_friendship_levels": {"blue": 1, "green": 1, "yellow": 0, "max": 0},
    }
    },
    "state_validation": {"valid": True},
  }


def _training_action():
  action = Action()
  action.func = "do_training"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = {
    "name": "speed",
    "score_tuple": (24.0, 0),
    "weighted_stat_score": 24.0,
    "stat_gains": {"speed": 16, "power": 4, "sp": 8},
    "failure": 3,
    "total_supports": 3,
    "total_rainbow_friends": 1,
    "total_friendship_levels": {"blue": 1, "green": 1, "yellow": 0, "max": 0},
  }
  action["available_trainings"] = {
    "speed": copy.deepcopy(action["training_data"]),
  }
  return action


def _rest_action():
  action = _training_action()
  action.func = "do_rest"
  action["energy_level"] = 5
  return action


def _race_action():
  action = _training_action()
  action.func = "do_race"
  action["race_name"] = "any"
  return action


def _bare_race_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_race", "do_training", "do_rest"]
  action["race_name"] = "any"
  action["training_function"] = "stat_weight_training"
  action["available_trainings"] = {
    "speed": {
      "name": "speed",
      "score_tuple": (24.0, 0),
      "weighted_stat_score": 24.0,
      "stat_gains": {"speed": 16, "power": 4, "sp": 8},
      "failure": 3,
      "total_supports": 3,
      "total_rainbow_friends": 1,
      "total_friendship_levels": {"blue": 1, "green": 1, "yellow": 0, "max": 0},
    },
  }
  return action


def _snapshot(state_obj, action, note):
  snapshot = build_review_snapshot(state_obj, action, reasoning_notes=note, ocr_debug=[])
  assert "Path: planner" in snapshot.get("turn_discussion_text", ""), note
  return snapshot


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  bot.set_trackblazer_use_new_planner_enabled(True)

  try:
    pre_debut_state = _base_state()
    pre_debut_state["year"] = "Junior Year Pre-Debut"
    pre_debut_action = _race_action()
    activation = _activate_trackblazer_planner_turn(pre_debut_state, pre_debut_action)
    assert activation.get("status") == "planner"
    pre_debut_snapshot = _snapshot(pre_debut_state, pre_debut_action, "pre_debut")
    pre_debut_plan = (pre_debut_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
    assert (pre_debut_snapshot.get("planned_actions") or {}).get("race_check", {}).get("branch_kind") == "training"
    assert (pre_debut_snapshot.get("selected_action") or {}).get("func") == "do_training"
    assert (pre_debut_snapshot.get("selected_action") or {}).get("training_name") == "speed"
    assert ((pre_debut_plan.get("race_plan") or {}).get("race_scout") or {}).get("required") is False
    assert "race branch locked" in (((pre_debut_snapshot.get("selected_action") or {}).get("trackblazer_race_decision") or {}).get("reason") or "")

    bare_pre_debut_state = _base_state()
    bare_pre_debut_state["year"] = "Junior Year Pre-Debut"
    bare_pre_debut_action = _bare_race_action()
    activation = _activate_trackblazer_planner_turn(bare_pre_debut_state, bare_pre_debut_action)
    assert activation.get("status") == "planner"
    bare_pre_debut_snapshot = _snapshot(bare_pre_debut_state, bare_pre_debut_action, "bare_pre_debut")
    bare_pre_debut_plan = (bare_pre_debut_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
    assert (bare_pre_debut_snapshot.get("selected_action") or {}).get("func") == "do_training"
    assert (bare_pre_debut_snapshot.get("selected_action") or {}).get("training_name") == "speed"
    assert ((bare_pre_debut_plan.get("item_plan") or {}).get("selected_action_binding") or {}).get("func") == "do_training"
    assert ((bare_pre_debut_snapshot.get("planned_clicks") or [])[0] or {}).get("label") == "Open training menu"

    direct_pre_debut_state = _base_state()
    direct_pre_debut_state["year"] = "Junior Year Pre-Debut"
    direct_pre_debut_action = _race_action()
    direct_pre_debut_snapshot = build_review_snapshot(
      direct_pre_debut_state,
      direct_pre_debut_action,
      reasoning_notes="direct_pre_debut_snapshot",
      ocr_debug=[],
    )
    direct_pre_debut_plan = (direct_pre_debut_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
    assert (direct_pre_debut_snapshot.get("selected_action") or {}).get("func") == "do_training"
    assert ((direct_pre_debut_plan.get("item_plan") or {}).get("selected_action_binding") or {}).get("func") == "do_training"
    assert ((direct_pre_debut_snapshot.get("planned_clicks") or [])[0] or {}).get("label") == "Open training menu"
    assert not any(
      label.startswith("Open race menu")
      for label in [entry.get("label") or "" for entry in (direct_pre_debut_snapshot.get("planned_clicks") or [])]
    )

    legacy_rest_pre_debut_state = _base_state()
    legacy_rest_pre_debut_state["year"] = "Junior Year Pre-Debut"
    legacy_rest_pre_debut_state["energy_level"] = 100
    legacy_rest_pre_debut_state["max_energy"] = 100
    legacy_rest_pre_debut_action = _rest_action()
    legacy_rest_pre_debut_action["training_data"] = copy.deepcopy(
      legacy_rest_pre_debut_state["training_results"]["speed"]
    )
    legacy_rest_pre_debut_action["available_trainings"] = copy.deepcopy(
      legacy_rest_pre_debut_state["training_results"]
    )
    activation = _activate_trackblazer_planner_turn(
      legacy_rest_pre_debut_state,
      legacy_rest_pre_debut_action,
    )
    assert activation.get("status") == "planner"
    legacy_rest_pre_debut_snapshot = _snapshot(
      legacy_rest_pre_debut_state,
      legacy_rest_pre_debut_action,
      "legacy_rest_pre_debut",
    )
    assert (legacy_rest_pre_debut_snapshot.get("selected_action") or {}).get("func") == "do_training"
    assert (legacy_rest_pre_debut_snapshot.get("selected_action") or {}).get("training_name") == "speed"
    assert ((legacy_rest_pre_debut_snapshot.get("planned_clicks") or [])[0] or {}).get("label") == "Open training menu"

    stale_training_payload_state = _base_state()
    stale_training_payload_state["year"] = "Junior Year Pre-Debut"
    stale_training_payload_action = _rest_action()
    stale_training_payload_action["available_trainings"] = {
      "speed": {"name": "speed"},
    }
    activation = _activate_trackblazer_planner_turn(
      stale_training_payload_state,
      stale_training_payload_action,
    )
    assert activation.get("status") == "planner"
    stale_training_payload_snapshot = _snapshot(
      stale_training_payload_state,
      stale_training_payload_action,
      "stale_training_payload_pre_debut",
    )
    assert (stale_training_payload_snapshot.get("selected_action") or {}).get("func") == "do_training"
    assert (stale_training_payload_snapshot.get("selected_action") or {}).get("training_name") == "speed"
    assert ((stale_training_payload_snapshot.get("planned_clicks") or [])[0] or {}).get("label") == "Open training menu"

    forced_state = _base_state()
    forced_state["turn"] = "Race Day"
    forced_action = _training_action()
    activation = _activate_trackblazer_planner_turn(forced_state, forced_action)
    assert activation.get("status") == "planner"
    forced_snapshot = _snapshot(forced_state, forced_action, "forced_race_day")
    assert (forced_snapshot.get("planned_actions") or {}).get("race_check", {}).get("branch_kind") == "forced_race_day"
    assert (forced_snapshot.get("selected_action") or {}).get("is_race_day") is True

    scheduled_state = _base_state()
    scheduled_action = _training_action()
    scheduled_probe = _race_action()
    scheduled_probe["scheduled_race"] = True
    scheduled_probe["race_name"] = "tokyo_yushun"
    with patch("core.trackblazer.planner._scheduled_race_probe", return_value=scheduled_probe):
      activation = _activate_trackblazer_planner_turn(scheduled_state, scheduled_action)
      assert activation.get("status") == "planner"
      scheduled_snapshot = _snapshot(scheduled_state, scheduled_action, "scheduled_race")
    scheduled_warning = (scheduled_snapshot.get("planned_actions") or {}).get("race_warning_policy") or {}
    assert (scheduled_snapshot.get("planned_actions") or {}).get("race_check", {}).get("branch_kind") == "scheduled_race"
    assert scheduled_warning.get("accept_warning") is True
    assert scheduled_warning.get("force_accept_warning") is True

    rival_state = _base_state()
    rival_state["rival_indicator_detected"] = True
    rival_state["criteria"] = "Achieved"
    rival_action = _training_action()
    rival_decision = {
      "should_race": True,
      "reason": "Rival present with weak training; planner should scout before committing.",
      "training_total_stats": 12,
      "training_score": 12.0,
      "prefer_rival_race": True,
      "fallback_non_rival_race": False,
      "race_tier_target": "any",
      "race_name": "any",
      "race_available": True,
      "rival_indicator": True,
    }
    with patch("core.trackblazer.planner.evaluate_trackblazer_race", return_value=rival_decision):
      activation = _activate_trackblazer_planner_turn(rival_state, rival_action)
      assert activation.get("status") == "planner"
      rival_snapshot = _snapshot(rival_state, rival_action, "optional_rival_race")
    rival_plan = (rival_state.get("trackblazer_planner_state") or {}).get("turn_plan") or {}
    assert (rival_snapshot.get("planned_actions") or {}).get("race_check", {}).get("branch_kind") == "optional_rival_race"
    assert ((rival_plan.get("race_plan") or {}).get("race_scout") or {}).get("required") is True
    fallback_chain = ((rival_plan.get("fallback_policy") or {}).get("chain") or [])
    assert any(entry.get("trigger") == "rival_scout_failed" for entry in fallback_chain)

    fallback_race_state = _base_state()
    fallback_race_state["criteria"] = "Achieved"
    fallback_race_action = _training_action()
    fallback_race_decision = {
      "should_race": True,
      "reason": "Weak board fallback race branch.",
      "training_total_stats": 10,
      "training_score": 10.0,
      "prefer_rival_race": False,
      "fallback_non_rival_race": True,
      "race_tier_target": "g3",
      "race_name": "any",
      "race_available": True,
      "rival_indicator": False,
    }
    with patch("core.trackblazer.planner.evaluate_trackblazer_race", return_value=fallback_race_decision):
      activation = _activate_trackblazer_planner_turn(fallback_race_state, fallback_race_action)
      assert activation.get("status") == "planner"
      fallback_race_snapshot = _snapshot(fallback_race_state, fallback_race_action, "optional_fallback_race")
    fallback_warning = (fallback_race_snapshot.get("planned_actions") or {}).get("race_warning_policy") or {}
    assert (fallback_race_snapshot.get("planned_actions") or {}).get("race_check", {}).get("branch_kind") == "optional_fallback_race"
    assert fallback_warning.get("accept_warning") is False
    assert fallback_warning.get("cancel_target_label") == "rest"

    rest_rival_state = _base_state()
    rest_rival_state["rival_indicator_detected"] = True
    rest_rival_state["criteria"] = "Achieved"
    rest_rival_action = _rest_action()
    rest_rival_decision = {
      "should_race": True,
      "reason": "Rest was selected but rival race is better if warning allows it.",
      "training_total_stats": 0,
      "training_score": 0.0,
      "prefer_rival_race": True,
      "fallback_non_rival_race": False,
      "race_tier_target": "any",
      "race_name": "any",
      "race_available": True,
      "rival_indicator": True,
    }
    with patch("core.trackblazer.planner.evaluate_trackblazer_race", return_value=rest_rival_decision):
      activation = _activate_trackblazer_planner_turn(rest_rival_state, rest_rival_action)
      assert activation.get("status") == "planner"
      rest_rival_snapshot = _snapshot(rest_rival_state, rest_rival_action, "rest_promoted_rival")
    rest_warning = (rest_rival_snapshot.get("planned_actions") or {}).get("race_warning_policy") or {}
    assert rest_warning.get("accept_warning") is False
    assert rest_warning.get("cancel_target_label") == "rest"

    fallback_label_state = _base_state()
    fallback_label_action = _training_action()
    with patch("core.trackblazer.planner._build_planner_race_plan", side_effect=RuntimeError("synthetic planner failure")):
      activation = _activate_trackblazer_planner_turn(fallback_label_state, fallback_label_action)
      assert activation.get("status") == "fallback"
    fallback_label_snapshot = build_review_snapshot(
      fallback_label_state,
      fallback_label_action,
      reasoning_notes="planner_fallback_label",
      ocr_debug=[],
    )
    assert "Path: planner→legacy (fallback)" in fallback_label_snapshot.get("turn_discussion_text", "")

    print("trackblazer planner milestone 5 checks: ok")
  finally:
    bot.set_trackblazer_use_new_planner_enabled(False)


if __name__ == "__main__":
  main()
