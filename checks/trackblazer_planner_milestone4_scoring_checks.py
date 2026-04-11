import copy
from contextlib import ExitStack
from unittest.mock import patch

import core.config as config
import core.trackblazer.derive as derive_module
import utils.constants as constants
from core.actions import Action
from core.trackblazer.models import TurnPlan
from core.trackblazer.planner import plan_once
from core.trackblazer_race_logic import evaluate_trackblazer_race


def _base_state():
  return {
    "year": "Senior Year Early Jul",
    "turn": 12,
    "criteria": "Reach the next fan target",
    "energy_level": 60,
    "max_energy": 100,
    "current_mood": "GOOD",
    "current_stats": {"spd": 700, "sta": 500, "pwr": 520, "guts": 300, "wit": 460, "sp": 240},
    "date_event_available": True,
    "race_mission_available": False,
    "aptitudes": {"track": "A", "distance": "A"},
    "status_effect_names": [],
    "rival_indicator_detected": False,
    "trackblazer_climax": False,
    "trackblazer_climax_locked_race": False,
    "trackblazer_climax_race_day": False,
    "trackblazer_lobby_scheduled_race": False,
    "trackblazer_inventory": {},
    "trackblazer_inventory_summary": {
      "held_quantities": {},
      "items_detected": [],
      "actionable_items": [],
      "by_category": {},
      "total_detected": 0,
    },
    "trackblazer_inventory_flow": {"opened": True, "closed": True},
    "trackblazer_shop_items": [],
    "trackblazer_shop_summary": {"shop_coins": 0, "items_detected": [], "purchasable_items": []},
    "trackblazer_shop_flow": {"entered": True, "closed": True},
    "skill_purchase_check": {"should_check": False},
    "skill_purchase_flow": {"skipped": True},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "training_results": {
      "speed": {
        "name": "speed",
        "score_tuple": (28.0, 0),
        "weighted_stat_score": 28.0,
        "stat_gains": {"speed": 15, "power": 5, "sp": 9},
        "failure": 2,
        "total_supports": 3,
        "total_rainbow_friends": 1,
        "total_friendship_levels": {"blue": 1, "green": 1, "yellow": 0, "max": 0},
      },
      "stamina": {
        "name": "stamina",
        "score_tuple": (18.0, 0),
        "weighted_stat_score": 18.0,
        "stat_gains": {"stamina": 10, "power": 3, "sp": 6},
        "failure": 1,
        "total_supports": 2,
        "total_rainbow_friends": 0,
        "total_friendship_levels": {"blue": 1, "green": 0, "yellow": 0, "max": 0},
      },
    },
    "state_validation": {"valid": True},
  }


def _base_action(state_obj):
  action = Action()
  action.func = "do_training"
  action.available_actions = ["do_training", "do_rest", "do_race"]
  action["training_name"] = "speed"
  action["training_function"] = "stat_weight_training"
  action["training_data"] = copy.deepcopy((state_obj.get("training_results") or {}).get("speed") or {})
  action["available_trainings"] = copy.deepcopy(state_obj.get("training_results") or {})
  return action


def _stub_planner_race_plan():
  return {
    "planner_owned": True,
    "branch_kind": "non_race",
    "selection_rationale": "",
    "selected_action": {
      "func": "",
      "trackblazer_race_decision": {},
      "trackblazer_race_lookahead": {},
    },
    "action_payload": {
      "planner_owned": True,
      "branch_kind": "non_race",
      "func": "",
      "options": {"func": ""},
      "fallback_action": {},
      "available_actions": ["do_training", "do_rest", "do_race"],
    },
    "race_check": {"planner_owned": True, "branch_kind": "non_race"},
    "race_decision": {},
    "race_entry_gate": {},
    "race_scout": {"planner_owned": True, "required": False, "executed": False},
    "warning_plan": {},
    "fallback_policy": {"planner_owned": True, "chain": []},
  }


def _plan_once_with_stubs(state_obj, action, *, lookahead=None):
  lookahead = lookahead if isinstance(lookahead, dict) else {
    "source": {},
    "next_turn_races_count": 0,
    "next_n_turns_races_count": 0,
    "projected_energy_deficit": False,
    "next_g1_distance": None,
    "next_race_day_distance": None,
  }
  with ExitStack() as stack:
    stack.enter_context(patch("core.trackblazer.planner._build_planner_race_plan", return_value=_stub_planner_race_plan()))
    stack.enter_context(patch("core.trackblazer.derive._lookahead_summary", return_value=copy.deepcopy(lookahead)))
    return plan_once(state_obj, action, limit=8)


def _assert_selected(turn_plan_snapshot, *, expected_node_id, expected_func, expected_training_name=None):
  turn_plan = TurnPlan.from_snapshot(turn_plan_snapshot)
  selected_candidate = dict(turn_plan.selected_candidate or {})
  assert selected_candidate.get("node_id") == expected_node_id, selected_candidate
  selected_action = dict((turn_plan.race_plan or {}).get("selected_action") or {})
  assert selected_action.get("func") == expected_func, selected_action
  if expected_training_name is not None:
    assert selected_action.get("training_name") == expected_training_name, selected_action


def main():
  config.reload_config(print_config=False)
  constants.SCENARIO_NAME = "trackblazer"
  derive_module._LOOKAHEAD_CACHE.clear()

  # PRD acceptance: weak training + rival visible + enough energy -> race:rival.
  case1_state = _base_state()
  case1_state["rival_indicator_detected"] = True
  case1_action = _base_action(case1_state)
  case1_plan = _plan_once_with_stubs(case1_state, case1_action)
  _assert_selected(case1_plan.get("turn_plan") or {}, expected_node_id="race:rival", expected_func="do_race")

  # Weak training + rival visible should still race when a stat manual is held.
  case1b_state = _base_state()
  case1b_state["rival_indicator_detected"] = True
  case1b_state["trackblazer_inventory"]["speed_manual"] = {"detected": True, "held_quantity": 1}
  case1b_state["trackblazer_inventory_summary"]["held_quantities"]["speed_manual"] = 1
  case1b_state["trackblazer_inventory_summary"]["items_detected"] = ["speed_manual"]
  case1b_state["trackblazer_inventory_summary"]["actionable_items"] = ["speed_manual"]
  case1b_state["trackblazer_inventory_summary"]["by_category"] = {"training_boost": ["speed_manual"]}
  case1b_state["trackblazer_inventory_summary"]["total_detected"] = 1
  case1b_action = _base_action(case1b_state)
  case1b_plan = _plan_once_with_stubs(case1b_state, case1b_action)
  _assert_selected(case1b_plan.get("turn_plan") or {}, expected_node_id="race:rival", expected_func="do_race")

  # Boundary: rival-visible boards race below 30 and train at 30+.
  case2_state = _base_state()
  case2_state["rival_indicator_detected"] = True
  case2_state["training_results"]["speed"]["weighted_stat_score"] = 29.0
  case2_state["training_results"]["speed"]["score_tuple"] = (29.0, 0)
  case2_action = _base_action(case2_state)
  case2_plan = _plan_once_with_stubs(case2_state, case2_action)
  _assert_selected(case2_plan.get("turn_plan") or {}, expected_node_id="race:rival", expected_func="do_race")

  # PRD acceptance: strong-enough training overrides optional rival race.
  case3_state = _base_state()
  case3_state["rival_indicator_detected"] = True
  case3_state["energy_level"] = 90
  case3_state["training_results"]["speed"]["weighted_stat_score"] = 30.0
  case3_state["training_results"]["speed"]["score_tuple"] = (30.0, 0)
  case3_action = _base_action(case3_state)
  case3_plan = _plan_once_with_stubs(case3_state, case3_action)
  _assert_selected(
    case3_plan.get("turn_plan") or {},
    expected_node_id="train:speed",
    expected_func="do_training",
    expected_training_name="speed",
  )

  # Pre-first-summer bond score should count toward keeping training over race.
  case3b_state = _base_state()
  case3b_state["year"] = "Classic Year Early Jun"
  case3b_state["rival_indicator_detected"] = True
  case3b_state["energy_level"] = 90
  case3b_state["training_results"]["speed"]["weighted_stat_score"] = 24.0
  case3b_state["training_results"]["speed"]["bond_boost"] = 10.0
  case3b_state["training_results"]["speed"]["score_tuple"] = (34.0, 0)
  case3b_state["training_results"]["speed"]["stat_gains"] = {"speed": 12, "power": 4, "sp": 8}
  case3b_state["training_results"]["speed"]["total_friendship_levels"] = {"blue": 1, "green": 0, "yellow": 0, "max": 0}
  case3b_action = _base_action(case3b_state)
  case3b_plan = _plan_once_with_stubs(case3b_state, case3b_action)
  _assert_selected(
    case3b_plan.get("turn_plan") or {},
    expected_node_id="train:speed",
    expected_func="do_training",
    expected_training_name="speed",
  )

  # Runtime race gate should use training score, not raw stat totals.
  runtime_score_state = _base_state()
  runtime_score_state["rival_indicator_detected"] = True
  runtime_score_state["energy_level"] = 90
  runtime_score_state["year"] = "Classic Year Early Jun"
  runtime_score_action = _base_action(runtime_score_state)
  runtime_score_action["training_data"]["weighted_stat_score"] = 24.0
  runtime_score_action["training_data"]["bond_boost"] = 10.0
  runtime_score_action["training_data"]["score_tuple"] = (34.0, 0)
  runtime_score_action["training_data"]["stat_gains"] = {"speed": 12, "power": 4, "sp": 8}
  runtime_decision = evaluate_trackblazer_race(runtime_score_state, runtime_score_action)
  assert runtime_decision.get("should_race") is False, runtime_decision
  assert runtime_decision.get("training_score") == 34.0, runtime_decision

  # A high-stat but low-score board should still race when score is below threshold.
  runtime_weak_state = _base_state()
  runtime_weak_state["rival_indicator_detected"] = True
  runtime_weak_state["energy_level"] = 90
  runtime_weak_action = _base_action(runtime_weak_state)
  runtime_weak_action["training_data"]["weighted_stat_score"] = 28.0
  runtime_weak_action["training_data"]["score_tuple"] = (28.0, 0)
  runtime_weak_action["training_data"]["stat_gains"] = {"speed": 20, "power": 12, "sp": 8}
  runtime_weak_decision = evaluate_trackblazer_race(runtime_weak_state, runtime_weak_action)
  assert runtime_weak_decision.get("should_race") is True, runtime_weak_decision

  # No rival marker: a normal race is still valid when the board is under 30.
  case4_state = _base_state()
  case4_state["year"] = "Senior Year Late Feb"
  case4_state["rival_indicator_detected"] = False
  case4_state["training_results"]["speed"]["weighted_stat_score"] = 28.0
  case4_state["training_results"]["speed"]["score_tuple"] = (28.0, 0)
  case4_action = _base_action(case4_state)
  case4_plan = _plan_once_with_stubs(case4_state, case4_action)
  _assert_selected(case4_plan.get("turn_plan") or {}, expected_node_id="race:fallback", expected_func="do_race")

  # PRD acceptance: item-assisted training beats rest/rival on high-failure board.
  case5_state = _base_state()
  case5_state["rival_indicator_detected"] = True
  case5_state["energy_level"] = 85
  case5_state["training_results"]["speed"]["weighted_stat_score"] = 38.0
  case5_state["training_results"]["speed"]["score_tuple"] = (38.0, 0)
  case5_state["training_results"]["speed"]["failure"] = 32
  case5_state["training_results"]["speed"]["failure_bypassed_by_items"] = True
  case5_state["trackblazer_inventory"]["rich_hand_cream"] = {"detected": True, "held_quantity": 1}
  case5_state["trackblazer_inventory_summary"]["held_quantities"]["rich_hand_cream"] = 1
  case5_state["trackblazer_inventory_summary"]["items_detected"] = ["rich_hand_cream"]
  case5_state["trackblazer_inventory_summary"]["actionable_items"] = ["rich_hand_cream"]
  case5_state["trackblazer_inventory_summary"]["by_category"] = {"support": ["rich_hand_cream"]}
  case5_state["trackblazer_inventory_summary"]["total_detected"] = 1
  case5_action = _base_action(case5_state)
  case5_plan = _plan_once_with_stubs(
    case5_state,
    case5_action,
    lookahead={
      "source": {},
      "next_turn_races_count": 1,
      "next_n_turns_races_count": 2,
      "projected_energy_deficit": False,
      "next_g1_distance": 1,
      "next_race_day_distance": 1,
    },
  )
  _assert_selected(
    case5_plan.get("turn_plan") or {},
    expected_node_id="train:speed+items:rich_hand_cream",
    expected_func="do_training",
    expected_training_name="speed",
  )

  # PRD acceptance: critical energy + near race cadence -> rest.
  case6_state = _base_state()
  case6_state["energy_level"] = 8
  case6_state["rival_indicator_detected"] = False
  case6_action = _base_action(case6_state)
  case6_plan = _plan_once_with_stubs(
    case6_state,
    case6_action,
    lookahead={
      "source": {},
      "next_turn_races_count": 1,
      "next_n_turns_races_count": 2,
      "projected_energy_deficit": True,
      "next_g1_distance": 2,
      "next_race_day_distance": 1,
    },
  )
  _assert_selected(case6_plan.get("turn_plan") or {}, expected_node_id="rest", expected_func="do_rest")

  # Regression: rival-visible strong summer training should not collapse to
  # rest just because lookahead claims an energy deficit.
  case7_state = _base_state()
  case7_state["year"] = "Classic Year Late Aug"
  case7_state["turn"] = 9
  case7_state["energy_level"] = 66
  case7_state["max_energy"] = 126
  case7_state["current_mood"] = "GREAT"
  case7_state["rival_indicator_detected"] = True
  case7_state["training_results"] = {
    "pwr": {
      "name": "pwr",
      "score_tuple": (41.0, 0),
      "weighted_stat_score": 41.0,
      "stat_gains": {"sta": 16, "pwr": 25, "sp": 4},
      "failure": 4,
      "total_supports": 2,
      "total_rainbow_friends": 1,
      "total_friendship_levels": {"yellow": 2, "max": 0, "gray": 0, "blue": 0, "green": 0},
    },
    "wit": {
      "name": "wit",
      "score_tuple": (27.0, 0),
      "weighted_stat_score": 27.0,
      "stat_gains": {"spd": 10, "wit": 17, "sp": 5},
      "failure": 0,
      "total_supports": 2,
      "total_rainbow_friends": 0,
      "total_friendship_levels": {"yellow": 2},
    },
  }
  case7_state["trackblazer_inventory"]["motivating_megaphone"] = {"detected": True, "held_quantity": 1}
  case7_state["trackblazer_inventory_summary"]["held_quantities"]["motivating_megaphone"] = 1
  case7_state["trackblazer_inventory_summary"]["items_detected"] = ["motivating_megaphone"]
  case7_state["trackblazer_inventory_summary"]["actionable_items"] = ["motivating_megaphone"]
  case7_state["trackblazer_inventory_summary"]["by_category"] = {"support": ["motivating_megaphone"]}
  case7_state["trackblazer_inventory_summary"]["total_detected"] = 1
  case7_action = Action()
  case7_action.func = "do_training"
  case7_action.available_actions = ["do_training", "do_rest", "do_race"]
  case7_action["training_name"] = "pwr"
  case7_action["training_function"] = "stat_weight_training"
  case7_action["training_data"] = copy.deepcopy(case7_state["training_results"]["pwr"])
  case7_action["available_trainings"] = copy.deepcopy(case7_state["training_results"])
  case7_plan = _plan_once_with_stubs(
    case7_state,
    case7_action,
    lookahead={
      "source": {"reason": "synthetic deficit"},
      "next_turn_races_count": 1,
      "next_n_turns_races_count": 2,
      "projected_energy_deficit": True,
      "next_g1_distance": 1,
      "next_race_day_distance": 1,
    },
  )
  _assert_selected(
    case7_plan.get("turn_plan") or {},
    expected_node_id="train:pwr+items:motivating_megaphone",
    expected_func="do_training",
    expected_training_name="pwr",
  )

  print("trackblazer planner milestone 4 scoring checks: ok")


if __name__ == "__main__":
  main()
