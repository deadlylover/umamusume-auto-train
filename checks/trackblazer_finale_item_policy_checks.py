import unittest

import core.config as config
import utils.constants as constants
from core.actions import Action
from core.trackblazer import planner as planner_module
from core.trackblazer_item_use import plan_item_usage


def _base_state():
  return {
    "year": "Senior Year Early Jul",
    "turn": 12,
    "criteria": "Reach the next fan target",
    "energy_level": 65,
    "max_energy": 100,
    "current_mood": "GOOD",
    "current_stats": {
      "spd": 800,
      "sta": 620,
      "pwr": 700,
      "guts": 310,
      "wit": 540,
      "sp": 240,
    },
    "date_event_available": True,
    "race_mission_available": False,
    "aptitudes": {"track": "A", "distance": "A"},
    "status_effect_names": [],
    "rival_indicator_detected": False,
    "trackblazer_climax": False,
    "trackblazer_climax_locked_race": False,
    "trackblazer_trainings_remaining_upper_bound": 6,
    "training_results": {
      "speed": {
        "name": "speed",
        "score_tuple": (28.0, 0),
        "weighted_stat_score": 28.0,
        "stat_gains": {"speed": 18, "power": 7, "sp": 10},
        "failure": 1,
        "total_supports": 3,
        "total_rainbow_friends": 0,
        "total_friendship_levels": {"gray": 0, "blue": 2, "green": 1, "yellow": 0, "max": 0},
        "failure_bypassed_by_items": False,
      },
    },
    "trackblazer_inventory": {
      "grilled_carrots": {"detected": True, "held_quantity": 1, "increment_target": (1, 1), "category": "bond"},
      "master_cleat_hammer": {"detected": True, "held_quantity": 1, "increment_target": (2, 2), "category": "race"},
    },
    "trackblazer_inventory_summary": {
      "held_quantities": {"grilled_carrots": 1, "master_cleat_hammer": 1},
      "items_detected": ["grilled_carrots", "master_cleat_hammer"],
      "actionable_items": ["grilled_carrots", "master_cleat_hammer"],
      "by_category": {"bond": ["grilled_carrots"], "race": ["master_cleat_hammer"]},
      "total_detected": 2,
    },
    "trackblazer_shop_items": [],
    "trackblazer_shop_summary": {"shop_coins": -1, "items_detected": [], "purchasable_items": []},
    "skill_purchase_check": {
      "should_check": False,
      "current_sp": 240,
      "threshold_sp": 400,
      "auto_buy_skill_enabled": False,
      "reason": "below threshold",
    },
    "skill_purchase_flow": {"skipped": True, "reason": "below threshold", "timing_total": 0.05},
    "skill_purchase_scan": {},
    "skill_purchase_plan": {},
    "state_validation": {"valid": True},
    "trackblazer_shop_priority_preview": [],
  }


class TrackblazerFinaleItemPolicyChecks(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    config.reload_config(print_config=False)
    constants.SCENARIO_NAME = "trackblazer"

  def test_grilled_carrots_are_not_auto_used_on_race_turns(self):
    state_obj = _base_state()
    state_obj["year"] = "Finale Underway"
    state_obj["turn"] = 1
    state_obj["trackblazer_climax"] = True
    action = Action()
    action.func = "do_race"
    action["race_name"] = "any"
    action["trackblazer_climax_race_day"] = True
    action["is_race_day"] = True
    action["training_name"] = "speed"
    action["training_data"] = dict(state_obj["training_results"]["speed"])
    action["available_trainings"] = dict(state_obj["training_results"])

    plan = plan_item_usage(
      policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
      state_obj=state_obj,
      action=action,
      limit=8,
    )

    candidate_keys = [entry.get("key") for entry in (plan.get("candidates") or [])]
    self.assertNotIn("grilled_carrots", candidate_keys)
    self.assertIn("master_cleat_hammer", candidate_keys)

  def test_finale_underway_forces_climax_race_day_even_without_template_flag(self):
    state_obj = _base_state()
    state_obj["year"] = "Finale Underway"
    state_obj["turn"] = 1
    state_obj["trackblazer_climax"] = True
    state_obj["trackblazer_climax_race_day"] = False
    state_obj["trackblazer_climax_locked_race"] = False

    action = Action()
    action.func = "do_training"
    action.available_actions = ["do_training", "do_rest", "do_race"]
    action["training_name"] = "speed"
    action["training_function"] = "stat_weight_training"
    action["training_data"] = dict(state_obj["training_results"]["speed"])
    action["available_trainings"] = dict(state_obj["training_results"])

    plan = planner_module.plan_once(state_obj, action, limit=8)
    turn_plan = plan.get("turn_plan") or {}
    review_context = dict(turn_plan.get("review_context") or {})
    selected_action = dict(review_context.get("selected_action") or {})

    self.assertEqual(selected_action.get("func"), "do_race")
    self.assertTrue(selected_action.get("is_race_day"))
    self.assertTrue(selected_action.get("trackblazer_climax_race_day"))
    self.assertEqual((turn_plan.get("race_plan") or {}).get("branch_kind"), "forced_climax_race")


if __name__ == "__main__":
  unittest.main()
