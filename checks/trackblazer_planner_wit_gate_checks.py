import unittest

import core.config as config
import utils.constants as constants
from core.trackblazer.planner import _score_planner_native_candidates


class TrackblazerPlannerWitGateChecks(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    config.reload_config(print_config=False)
    constants.SCENARIO_NAME = "trackblazer"

  def test_dead_wit_is_dropped_and_rest_wins_when_other_trainings_fail(self):
    observed_data = {
      "year": "Junior Year Pre-Debut",
      "turn": 5,
      "energy_level": 58.1,
      "max_energy": 125.8,
      "current_mood": "GOOD",
      "training_results": {
        "spd": {
          "failure": 12,
          "total_supports": 2,
          "total_rainbow_friends": 0,
        },
        "pwr": {
          "failure": 10,
          "total_supports": 2,
          "total_rainbow_friends": 0,
        },
        "sta": {
          "failure": 10,
          "total_supports": 1,
          "total_rainbow_friends": 0,
        },
        "guts": {
          "failure": 13,
          "total_supports": 0,
          "total_rainbow_friends": 0,
        },
        "wit": {
          "failure": 0,
          "total_supports": 0,
          "total_rainbow_friends": 0,
        },
      },
      "missing_inputs": [],
      "rival_indicator_detected": False,
      "trackblazer_lobby_scheduled_race": False,
      "race_mission_available": False,
      "trackblazer_climax_locked_race": False,
      "trackblazer_climax_race_day": False,
      "status_effect_names": [],
    }
    derived_data = {
      "energy_ratio": observed_data["energy_level"] / observed_data["max_energy"],
      "training_value": [
        {
          "name": "spd",
          "score": 40.0,
          "total_stat_gain": 20.0,
          "matching_stat_gain": 14.0,
          "failure": 12,
          "support_count": 2,
          "rainbow_count": 0,
          "value_class": "strong",
          "usage_context": {},
        },
        {
          "name": "pwr",
          "score": 38.6,
          "total_stat_gain": 15.0,
          "matching_stat_gain": 6.0,
          "failure": 10,
          "support_count": 2,
          "rainbow_count": 0,
          "value_class": "strong",
          "usage_context": {},
        },
        {
          "name": "sta",
          "score": 26.4,
          "total_stat_gain": 14.0,
          "matching_stat_gain": 10.0,
          "failure": 10,
          "support_count": 1,
          "rainbow_count": 0,
          "value_class": "ok",
          "usage_context": {},
        },
        {
          "name": "guts",
          "score": 10.2,
          "total_stat_gain": 13.0,
          "matching_stat_gain": 7.0,
          "failure": 13,
          "support_count": 0,
          "rainbow_count": 0,
          "value_class": "weak",
          "usage_context": {},
        },
        {
          "name": "wit",
          "score": 11.0,
          "total_stat_gain": 11.0,
          "matching_stat_gain": 9.0,
          "failure": 0,
          "support_count": 0,
          "rainbow_count": 0,
          "value_class": "weak",
          "usage_context": {},
        },
      ],
      "lookahead_summary": {},
      "race_opportunity": {},
      "timeline_policy": {
        "optional_races_allowed": True,
      },
    }
    candidates = [
      {"node_id": "train:spd", "requirements": [], "rationale": "speed candidate"},
      {"node_id": "train:pwr", "requirements": [], "rationale": "power candidate"},
      {"node_id": "train:sta", "requirements": [], "rationale": "stamina candidate"},
      {"node_id": "train:guts", "requirements": [], "rationale": "guts candidate"},
      {"node_id": "train:wit", "requirements": [], "rationale": "wit candidate"},
      {"node_id": "rest", "requirements": [], "rationale": "rest candidate"},
    ]

    ranked = _score_planner_native_candidates(
      observed_data,
      derived_data,
      getattr(config, "TRACKBLAZER_PLANNER_POLICY", {}),
      candidates,
    )

    self.assertEqual(ranked[0]["node_id"], "rest")

    wit_entry = next(entry for entry in ranked if entry["node_id"] == "train:wit")
    self.assertEqual(wit_entry["priority_score"], float("-inf"))
    self.assertIn("wit gate blocked", wit_entry["rationale"])

  def test_high_energy_override_keeps_wit_viable(self):
    observed_data = {
      "year": "Junior Year Debut",
      "turn": 6,
      "energy_level": 105.0,
      "max_energy": 125.0,
      "training_results": {
        "wit": {
          "failure": 0,
          "total_supports": 0,
          "total_rainbow_friends": 0,
        },
      },
      "missing_inputs": [],
    }
    derived_data = {
      "energy_ratio": observed_data["energy_level"] / observed_data["max_energy"],
      "training_value": [
        {
          "name": "wit",
          "score": 11.0,
          "total_stat_gain": 11.0,
          "matching_stat_gain": 9.0,
          "failure": 0,
          "support_count": 0,
          "rainbow_count": 0,
          "value_class": "weak",
          "usage_context": {},
        },
      ],
      "lookahead_summary": {},
      "race_opportunity": {},
      "timeline_policy": {
        "optional_races_allowed": True,
      },
    }
    candidates = [
      {"node_id": "train:wit", "requirements": [], "rationale": "wit candidate"},
      {"node_id": "rest", "requirements": [], "rationale": "rest candidate"},
    ]

    ranked = _score_planner_native_candidates(
      observed_data,
      derived_data,
      getattr(config, "TRACKBLAZER_PLANNER_POLICY", {}),
      candidates,
    )

    wit_entry = next(entry for entry in ranked if entry["node_id"] == "train:wit")
    self.assertNotEqual(wit_entry["priority_score"], float("-inf"))
    self.assertTrue(wit_entry["source_facts"]["wit_failure_gate"]["allowed"])


if __name__ == "__main__":
  unittest.main()
