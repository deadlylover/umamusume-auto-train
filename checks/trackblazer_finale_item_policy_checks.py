import unittest

import core.config as config
import utils.constants as constants
from core.actions import Action
from core.trackblazer import planner as planner_module
from core.trackblazer_race_logic import evaluate_trackblazer_race
from core.trackblazer_item_use import plan_item_usage
from core.trackblazer.timeline_policy import get_trackblazer_timeline_policy


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

  def test_finale_underway_prefers_training_with_reset_whistle_over_optional_race(self):
    state_obj = _base_state()
    state_obj["year"] = "Finale Underway"
    state_obj["turn"] = 1
    state_obj["trackblazer_climax"] = True
    state_obj["trackblazer_climax_race_day"] = False
    state_obj["trackblazer_climax_locked_race"] = False
    state_obj["criteria"] = "Win the Twinkle Star Climax series Current Rank RANK 1"
    state_obj["energy_level"] = 36
    state_obj["max_energy"] = 130
    state_obj["trackblazer_inventory"]["reset_whistle"] = {
      "detected": True,
      "held_quantity": 2,
      "increment_target": (3, 3),
      "category": "condition",
    }
    state_obj["trackblazer_inventory_summary"]["held_quantities"]["reset_whistle"] = 2
    state_obj["trackblazer_inventory_summary"]["items_detected"].append("reset_whistle")
    state_obj["trackblazer_inventory_summary"]["actionable_items"].append("reset_whistle")
    state_obj["training_results"]["speed"]["score_tuple"] = (17.0, 0)
    state_obj["training_results"]["speed"]["weighted_stat_score"] = 17.0
    state_obj["training_results"]["speed"]["failure"] = 8

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
    item_plan = dict(turn_plan.get("item_plan") or {})
    use_now = [
      entry.get("key")
      for entry in list(((item_plan.get("execution_payload") or {}).get("execution_items") or []))
    ]

    self.assertEqual(selected_action.get("func"), "do_training")
    self.assertFalse(selected_action.get("trackblazer_climax_race_day"))
    self.assertEqual((turn_plan.get("race_plan") or {}).get("branch_kind"), "training")
    self.assertIn("reset_whistle", use_now)

  def test_finale_underway_safe_training_stays_training_without_whistle(self):
    state_obj = _base_state()
    state_obj["year"] = "Finale Underway"
    state_obj["turn"] = 1
    state_obj["trackblazer_climax"] = True
    state_obj["trackblazer_climax_race_day"] = False
    state_obj["criteria"] = "Win the Twinkle Star Climax series Current Rank RANK 1"
    state_obj["energy_level"] = 82
    state_obj["max_energy"] = 130
    state_obj["training_results"]["speed"]["score_tuple"] = (18.0, 0)
    state_obj["training_results"]["speed"]["weighted_stat_score"] = 18.0
    state_obj["training_results"]["speed"]["failure"] = 0

    action = Action()
    action.func = "do_training"
    action["training_name"] = "speed"
    action["training_function"] = "stat_weight_training"
    action["training_data"] = dict(state_obj["training_results"]["speed"])
    action["available_trainings"] = dict(state_obj["training_results"])

    plan = planner_module.plan_once(state_obj, action, limit=8)
    turn_plan = plan.get("turn_plan") or {}
    review_context = dict(turn_plan.get("review_context") or {})
    selected_action = dict(review_context.get("selected_action") or {})
    item_plan = dict(turn_plan.get("item_plan") or {})
    use_now = [
      entry.get("key")
      for entry in list(((item_plan.get("execution_payload") or {}).get("execution_items") or []))
    ]

    self.assertEqual(selected_action.get("func"), "do_training")
    self.assertNotIn("reset_whistle", use_now)

  def test_finale_underway_prefers_item_committed_training_over_safe_wit(self):
    state_obj = _base_state()
    state_obj["year"] = "Finale Underway"
    state_obj["turn"] = 1
    state_obj["trackblazer_climax"] = True
    state_obj["trackblazer_climax_race_day"] = False
    state_obj["trackblazer_trainings_remaining_upper_bound"] = 3
    state_obj["criteria"] = "Win the Twinkle Star Climax series Current Rank RANK"
    state_obj["energy_level"] = 54
    state_obj["max_energy"] = 131
    state_obj["current_mood"] = "GREAT"
    state_obj["current_stats"] = {
      "spd": 1182,
      "sta": 691,
      "pwr": 992,
      "guts": 408,
      "wit": 612,
      "sp": 637,
    }
    state_obj["training_results"] = {
      "sta": {
        "name": "sta",
        "score_tuple": (25.2, 0),
        "weighted_stat_score": 25.2,
        "stat_gains": {"sta": 21, "guts": 7, "sp": 4},
        "failure": 15,
        "total_supports": 2,
        "total_rainbow_friends": 1,
        "total_friendship_levels": {"gray": 0, "blue": 0, "green": 0, "yellow": 1, "max": 0},
        "failure_bypassed_by_items": True,
        "trackblazer_failure_bypass_items": ["royal_kale_juice"],
      },
      "pwr": {
        "name": "pwr",
        "score_tuple": (24.0, 0),
        "weighted_stat_score": 24.0,
        "stat_gains": {"sta": 9, "pwr": 15, "sp": 3},
        "failure": 17,
        "total_supports": 1,
        "total_rainbow_friends": 1,
        "total_friendship_levels": {"gray": 0, "blue": 0, "green": 0, "yellow": 0, "max": 1},
        "failure_bypassed_by_items": True,
        "trackblazer_failure_bypass_items": ["royal_kale_juice"],
      },
      "wit": {
        "name": "wit",
        "score_tuple": (8.0, 0),
        "weighted_stat_score": 8.0,
        "stat_gains": {"spd": 2, "wit": 8, "sp": 0},
        "failure": 0,
        "total_supports": 0,
        "total_rainbow_friends": 0,
        "total_friendship_levels": {"gray": 0, "blue": 0, "green": 0, "yellow": 0, "max": 0},
      },
    }
    state_obj["trackblazer_inventory"] = {
      "royal_kale_juice": {"detected": True, "held_quantity": 2, "increment_target": (1, 1), "category": "energy"},
      "motivating_megaphone": {"detected": True, "held_quantity": 1, "increment_target": (2, 2), "category": "training_boost"},
      "stamina_ankle_weights": {"detected": True, "held_quantity": 1, "increment_target": (3, 3), "category": "training_boost"},
    }
    state_obj["trackblazer_inventory_summary"] = {
      "held_quantities": {
        "royal_kale_juice": 2,
        "motivating_megaphone": 1,
        "stamina_ankle_weights": 1,
      },
      "items_detected": ["royal_kale_juice", "motivating_megaphone", "stamina_ankle_weights"],
      "actionable_items": ["royal_kale_juice", "motivating_megaphone", "stamina_ankle_weights"],
      "by_category": {
        "energy": ["royal_kale_juice"],
        "training_boost": ["motivating_megaphone", "stamina_ankle_weights"],
      },
      "total_detected": 3,
    }

    action = Action()
    action.func = "do_training"
    action["training_name"] = "wit"
    action["training_function"] = "stat_weight_training"
    action["training_data"] = dict(state_obj["training_results"]["wit"])
    action["available_trainings"] = dict(state_obj["training_results"])

    plan = planner_module.plan_once(state_obj, action, limit=8)
    turn_plan = plan.get("turn_plan") or {}
    review_context = dict(turn_plan.get("review_context") or {})
    selected_action = dict(review_context.get("selected_action") or {})
    item_plan = dict(turn_plan.get("item_plan") or {})
    use_now = [
      entry.get("key")
      for entry in list(((item_plan.get("execution_payload") or {}).get("execution_items") or []))
    ]
    deferred_use = [
      entry.get("key")
      for entry in list(item_plan.get("deferred_use") or [])
    ]

    self.assertEqual(selected_action.get("func"), "do_training")
    self.assertNotEqual(selected_action.get("training_name"), "wit")
    self.assertIn("royal_kale_juice", use_now)
    self.assertIn("motivating_megaphone", deferred_use)

  def test_legacy_race_logic_suppresses_finale_optional_fallback_race(self):
    state_obj = _base_state()
    state_obj["year"] = "Finale Underway"
    state_obj["turn"] = 1
    state_obj["trackblazer_climax"] = True
    state_obj["trackblazer_climax_race_day"] = False
    state_obj["criteria"] = "Win the Twinkle Star Climax series Current Rank RANK 1"
    state_obj["energy_level"] = 36
    state_obj["max_energy"] = 130
    state_obj["trackblazer_inventory"]["reset_whistle"] = {
      "detected": True,
      "held_quantity": 1,
      "increment_target": (3, 3),
      "category": "condition",
    }
    state_obj["trackblazer_inventory_summary"]["held_quantities"]["reset_whistle"] = 1
    state_obj["trackblazer_inventory_summary"]["items_detected"].append("reset_whistle")
    state_obj["trackblazer_inventory_summary"]["actionable_items"].append("reset_whistle")
    state_obj["training_results"]["speed"]["score_tuple"] = (17.0, 0)
    state_obj["training_results"]["speed"]["weighted_stat_score"] = 17.0
    state_obj["training_results"]["speed"]["failure"] = 8

    action = Action()
    action.func = "do_training"
    action["training_name"] = "speed"
    action["training_data"] = dict(state_obj["training_results"]["speed"])
    action["available_trainings"] = dict(state_obj["training_results"])

    decision = evaluate_trackblazer_race(state_obj, action)

    self.assertFalse(decision.get("should_race"))
    self.assertTrue(decision.get("prefer_train_over_weak_training"))

  def test_timeline_policy_models_finale_training_turn_without_forced_race_signal(self):
    state_obj = _base_state()
    state_obj["year"] = "Finale Underway"
    state_obj["turn"] = "Finale Turn"
    state_obj["trackblazer_climax"] = True
    state_obj["trackblazer_climax_race_day"] = False
    state_obj["trackblazer_climax_race_day_banner"] = False
    state_obj["trackblazer_climax_race_day_button"] = False

    timeline_policy = get_trackblazer_timeline_policy(state_obj)

    self.assertTrue(timeline_policy.get("is_climax_window"))
    self.assertTrue(timeline_policy.get("is_finale_underway_training_turn"))
    self.assertFalse(timeline_policy.get("is_forced_climax_race_day"))
    self.assertFalse(timeline_policy.get("optional_races_allowed"))
    self.assertEqual(timeline_policy.get("trainings_remaining_upper_bound"), 6)

  def test_explicit_climax_race_signal_forces_race_branch(self):
    state_obj = _base_state()
    state_obj["year"] = "Finale Underway"
    state_obj["turn"] = "Finale Turn"
    state_obj["trackblazer_climax"] = True
    state_obj["trackblazer_climax_race_day"] = False
    state_obj["trackblazer_climax_race_day_banner"] = True
    state_obj["trackblazer_climax_race_day_button"] = False

    action = Action()
    action.func = "do_training"
    action["training_name"] = "speed"
    action["training_function"] = "stat_weight_training"
    action["training_data"] = dict(state_obj["training_results"]["speed"])
    action["available_trainings"] = dict(state_obj["training_results"])

    decision = evaluate_trackblazer_race(state_obj, action)
    plan = planner_module.plan_once(state_obj, action, limit=8)
    turn_plan = plan.get("turn_plan") or {}
    review_context = dict(turn_plan.get("review_context") or {})
    selected_action = dict(review_context.get("selected_action") or {})

    self.assertTrue(decision.get("should_race"))
    self.assertEqual((turn_plan.get("race_plan") or {}).get("branch_kind"), "forced_climax_race")
    self.assertEqual(selected_action.get("func"), "do_race")
    self.assertTrue(((selected_action.get("timeline_policy") or {}).get("is_forced_climax_race_day")))

  def test_summer_weak_board_keeps_existing_whistle_reroll_behavior(self):
    state_obj = _base_state()
    state_obj["year"] = "Senior Year Early Jul"
    state_obj["turn"] = 1
    state_obj["trackblazer_inventory"]["reset_whistle"] = {
      "detected": True,
      "held_quantity": 1,
      "increment_target": (3, 3),
      "category": "condition",
    }
    state_obj["trackblazer_inventory_summary"]["held_quantities"]["reset_whistle"] = 1
    state_obj["trackblazer_inventory_summary"]["items_detected"].append("reset_whistle")
    state_obj["trackblazer_inventory_summary"]["actionable_items"].append("reset_whistle")
    state_obj["training_results"]["speed"]["score_tuple"] = (4.0, 0)
    state_obj["training_results"]["speed"]["weighted_stat_score"] = 4.0
    state_obj["training_results"]["speed"]["failure"] = 9
    state_obj["training_results"]["speed"]["stat_gains"] = {"speed": 14, "sp": 5}
    state_obj["training_results"]["speed"]["total_rainbow_friends"] = 0

    action = Action()
    action.func = "do_training"
    action["training_name"] = "speed"
    action["training_function"] = "stat_weight_training"
    action["training_data"] = dict(state_obj["training_results"]["speed"])
    action["available_trainings"] = dict(state_obj["training_results"])

    plan = plan_item_usage(
      policy=getattr(config, "TRACKBLAZER_ITEM_USE_POLICY", None),
      state_obj=state_obj,
      action=action,
      limit=8,
    )
    candidate_keys = [entry.get("key") for entry in (plan.get("candidates") or [])]

    self.assertIn("reset_whistle", candidate_keys)
    self.assertTrue((plan.get("context") or {}).get("summer_window"))

  def test_pre_bond_cutoff_training_scoring_keeps_friendship_sensitive_preference(self):
    state_obj = _base_state()
    state_obj["year"] = "Classic Year Early May"
    state_obj["turn"] = 1
    state_obj["trackblazer_climax"] = False
    state_obj["trackblazer_climax_race_day"] = False
    state_obj["trackblazer_climax_race_day_banner"] = False
    state_obj["trackblazer_climax_race_day_button"] = False
    state_obj["training_results"] = {
      "speed": {
        "name": "speed",
        "score_tuple": (30.0, 0),
        "weighted_stat_score": 30.0,
        "stat_gains": {"speed": 20, "sp": 8},
        "failure": 0,
        "total_supports": 0,
        "total_rainbow_friends": 0,
        "total_friendship_levels": {"gray": 0, "blue": 0, "green": 0, "yellow": 0, "max": 0},
        "failure_bypassed_by_items": False,
      },
      "stamina": {
        "name": "stamina",
        "score_tuple": (29.0, 0),
        "weighted_stat_score": 29.0,
        "stat_gains": {"stamina": 15, "sp": 8},
        "failure": 0,
        "total_supports": 4,
        "total_rainbow_friends": 0,
        "total_friendship_levels": {"gray": 1, "blue": 2, "green": 1, "yellow": 0, "max": 0},
        "failure_bypassed_by_items": False,
      },
    }

    action = Action()
    action.func = "do_training"
    action["training_name"] = "speed"
    action["training_function"] = "stat_weight_training"
    action["training_data"] = dict(state_obj["training_results"]["speed"])
    action["available_trainings"] = dict(state_obj["training_results"])

    plan = planner_module.plan_once(state_obj, action, limit=8)
    turn_plan = plan.get("turn_plan") or {}
    review_context = dict(turn_plan.get("review_context") or {})
    selected_action = dict(review_context.get("selected_action") or {})
    timeline_policy = (selected_action.get("timeline_policy") or {})
    candidate_ranking = list(turn_plan.get("candidate_ranking") or [])
    training_candidates = [
      entry for entry in candidate_ranking
      if str(entry.get("node_id") or "").startswith("train:")
    ]

    self.assertTrue(timeline_policy.get("is_pre_bond_cutoff"))
    self.assertGreaterEqual(len(training_candidates), 2)
    self.assertEqual(training_candidates[0].get("node_id"), "train:stamina")


if __name__ == "__main__":
  unittest.main()
