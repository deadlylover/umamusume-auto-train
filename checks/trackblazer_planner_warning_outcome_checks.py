import json
from unittest.mock import patch

import utils.constants as constants
from core.actions import Action
from core.trackblazer.planner import (
  _build_planner_race_plan,
  plan_once,
  set_planner_consecutive_warning_outcome,
)


def _make_action():
  action = Action()
  action.func = "do_race"
  action.available_actions = ["do_race", "do_rest"]
  action["prefer_rival_race"] = True
  action["trackblazer_race_decision"] = {
    "should_race": True,
    "prefer_rival_race": True,
    "reason": "Rival present and action is rest.",
  }
  return action


def _make_state():
  return {
    "year": "Classic Year Early Jan",
    "turn": 24,
    "energy_level": 52.5,
    "max_energy": 125.8,
    "current_mood": "GOOD",
    "current_stats": {
      "spd": 404,
      "sta": 261,
      "pwr": 346,
      "guts": 228,
      "wit": 304,
      "sp": 73,
    },
    "training_results": {},
    "rival_indicator_detected": True,
  }


def main():
  constants.SCENARIO_NAME = "trackblazer"
  state_obj = _make_state()
  action = _make_action()
  set_planner_consecutive_warning_outcome(
    state_obj,
    {
      "cancelled": True,
      "force_rest": True,
      "reason": "optional_rival_promoted_from_rest",
    },
  )

  with patch("core.trackblazer.planner.get_trackblazer_timeline_policy", return_value={}):
    with patch("core.trackblazer.planner.get_race_lookahead_energy_advice", return_value={}):
      with patch("core.trackblazer.planner.evaluate_trackblazer_race", return_value={"should_race": True, "prefer_rival_race": True}):
        with patch("core.trackblazer.planner.get_effective_shop_items", return_value=[]):
          race_plan = _build_planner_race_plan(state_obj, action)
          planner_state = plan_once(state_obj, action, limit=8)

  selected_action = dict(race_plan.get("selected_action") or {})
  turn_plan = dict(planner_state.get("turn_plan") or {})
  turn_plan_selected = dict(((turn_plan.get("race_plan") or {}).get("selected_action") or {}))
  assert selected_action.get("func") == "do_rest", selected_action
  assert selected_action.get("planner_warning_outcome", {}).get("cancelled") is True, selected_action
  assert turn_plan_selected.get("func") == "do_rest", turn_plan_selected
  print(json.dumps({
    "status": "ok",
    "branch_kind": race_plan.get("branch_kind"),
    "selected_action": selected_action.get("func"),
    "turn_plan_selected_action": turn_plan_selected.get("func"),
    "warning_reason": selected_action.get("planner_warning_outcome", {}).get("reason"),
  }, indent=2, sort_keys=True))


if __name__ == "__main__":
  main()
