from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

import core.bot as bot
import core.config as config
import utils.constants as constants
from core.strategies import Strategy
from utils.shared import CleanDefaultDict


def _get_training_filter_settings(training_function):
  settings = {
    "use_risk_taking": False,
    "check_stat_caps": False,
  }
  if training_function in ("rainbow_training", "most_support_cards", "meta_training", "most_stat_gain", "stat_weight_training"):
    settings["use_risk_taking"] = True
  if training_function in ("rainbow_training", "most_support_cards", "meta_training", "stat_weight_training"):
    settings["check_stat_caps"] = True
  return settings


def resolve_review_risk_taking_set(state_obj, training_function):
  settings = _get_training_filter_settings(training_function)
  if not settings["use_risk_taking"] or not isinstance(training_function, str):
    return {}

  training_template = Strategy().get_training_template(state_obj) or {}
  risk_taking_set = training_template.get("risk_taking_set")
  return risk_taking_set if isinstance(risk_taking_set, dict) else {}


def summarize_training_exclusion(training_name, training_data, state_obj, training_function, risk_taking_set=None):
  settings = _get_training_filter_settings(training_function)
  current_stats = state_obj.get("current_stats") or {}
  current_stat = current_stats.get(training_name)
  stat_cap = config.STAT_CAPS.get(training_name)
  failure = training_data.get("failure")
  risk_increase = 0
  max_allowed_failure = config.MAX_FAILURE

  if settings["use_risk_taking"]:
    resolved_risk_taking_set = risk_taking_set if isinstance(risk_taking_set, dict) else {}
    if resolved_risk_taking_set:
      from core.trainings import calculate_risk_increase
      risk_increase = calculate_risk_increase(training_data, resolved_risk_taking_set)
      max_allowed_failure += risk_increase

  reason = "filtered"
  if settings["check_stat_caps"] and current_stat is not None and stat_cap is not None and current_stat >= stat_cap:
    reason = f"stat cap ({current_stat}/{stat_cap})"
  elif failure is not None and int(failure) > max_allowed_failure:
    reason = f"fail {int(failure)}% > {int(max_allowed_failure)}%"
  elif training_function:
    reason = f"not selected by {training_function}"

  return max_allowed_failure, risk_increase, reason


def score_training_for_display(training_name, training_data, state_obj, training_function, training_template):
  """Compute a score for a filtered-out training so it can be compared in review surfaces."""
  from core.trainings import (
    add_scenario_gimmick_score,
    max_out_friendships_score,
    most_stat_score,
    most_support_score,
    rainbow_training_score,
  )

  td = deepcopy(training_data)
  x = (training_name, td)
  try:
    if training_function == "meta_training":
      stat_gain = most_stat_score(x, state_obj, training_template)
      non_max = max_out_friendships_score(x)
      rainbow = rainbow_training_score(x)
      rainbow = add_scenario_gimmick_score(x, rainbow, state_obj)
      return ((stat_gain[0] / 10) + non_max[0] + rainbow[0], stat_gain[1])
    if training_function == "rainbow_training":
      rainbow = rainbow_training_score(x)
      rainbow = add_scenario_gimmick_score(x, rainbow, state_obj)
      non_max = max_out_friendships_score(x)
      return (rainbow[0] + non_max[0] * config.NON_MAX_SUPPORT_WEIGHT, rainbow[1])
    if training_function == "most_support_cards":
      support = most_support_score(x)
      support = add_scenario_gimmick_score(x, support, state_obj)
      non_max = max_out_friendships_score(x)
      return (non_max[0] * config.NON_MAX_SUPPORT_WEIGHT + support[0], support[1])
    if training_function == "most_stat_gain":
      return most_stat_score(x, state_obj, training_template)
    if training_function == "stat_weight_training":
      stat_weights = getattr(config, "TRACKBLAZER_STAT_WEIGHTS", None)
      if not isinstance(stat_weights, dict) or not stat_weights:
        stat_weights = training_template.get("stat_weight_set", {})
      stat_gains = td.get("stat_gains", {})
      total_value = 0
      current_stats = state_obj.get("current_stats") or {}
      for stat, gain in stat_gains.items():
        if stat == "sp":
          continue
        if current_stats.get(stat, 0) >= config.STAT_CAPS.get(stat, 9999):
          continue
        weight = stat_weights.get(stat, 1)
        total_value += gain * weight
      if bot.get_trackblazer_bond_boost_enabled():
        cutoff = bot.get_trackblazer_bond_boost_cutoff()
        current_year = state_obj.get("year", "")
        try:
          active = constants.TIMELINE.index(current_year) <= constants.TIMELINE.index(cutoff)
        except ValueError:
          active = False
        if active:
          friendship_levels = td.get("total_friendship_levels", {})
          raiseable = friendship_levels.get("blue", 0) + friendship_levels.get("green", 0)
          if raiseable > 0:
            per_friend = 15 if training_name == "wit" else 10
            total_value += raiseable * per_friend
      from core.trainings import get_priority_index
      priority_index = get_priority_index(x)
      return (total_value, -priority_index)
    if training_function == "max_out_friendships":
      max_f = max_out_friendships_score(x)
      max_f = add_scenario_gimmick_score(x, max_f, state_obj)
      rainbow = rainbow_training_score(x)
      return (max_f[0] + rainbow[0] * 0.25 * config.RAINBOW_SUPPORT_WEIGHT_ADDITION, max_f[1])
    return most_stat_score(x, state_obj, training_template)
  except Exception:
    return None


def build_ranked_training_snapshot(state_obj, available_trainings, training_function):
  raw_training_results = state_obj.get("training_results", {}) or {}
  merged_trainings = CleanDefaultDict()
  risk_taking_set = resolve_review_risk_taking_set(state_obj, training_function)

  training_template = None
  filtered_keys = set(raw_training_results.keys()) - set(available_trainings.keys())
  if filtered_keys:
    try:
      training_template = Strategy().get_training_template(state_obj) or {}
    except Exception:
      training_template = {}

  for training_name, training_data in raw_training_results.items():
    max_allowed_failure, risk_increase, exclusion_reason = summarize_training_exclusion(
      training_name=training_name,
      training_data=training_data,
      state_obj=state_obj,
      training_function=training_function,
      risk_taking_set=risk_taking_set,
    )

    score_tuple = None
    if training_name in filtered_keys and training_template is not None:
      score_tuple = score_training_for_display(
        training_name,
        training_data,
        state_obj,
        training_function,
        training_template,
      )

    merged_trainings[training_name] = {
      "name": training_name,
      "score_tuple": score_tuple,
      "failure": training_data.get("failure"),
      "max_allowed_failure": max_allowed_failure,
      "risk_increase": risk_increase,
      "total_supports": training_data.get("total_supports"),
      "total_rainbow_friends": None,
      "total_friendship_increases": None,
      "stat_gains": training_data.get("stat_gains"),
      "unity_gauge_fills": training_data.get("unity_gauge_fills"),
      "unity_spirit_explosions": training_data.get("unity_spirit_explosions"),
      "filtered_out": True,
      "excluded_reason": exclusion_reason,
    }

  for training_name, training_data in available_trainings.items():
    merged_trainings[training_name] = {
      "name": training_name,
      "score_tuple": training_data.get("score_tuple"),
      "failure": training_data.get("failure"),
      "max_allowed_failure": training_data.get("max_allowed_failure"),
      "risk_increase": training_data.get("risk_increase", 0),
      "total_supports": training_data.get("total_supports"),
      "total_rainbow_friends": training_data.get("total_rainbow_friends"),
      "total_friendship_increases": training_data.get("total_friendship_increases"),
      "stat_gains": training_data.get("stat_gains"),
      "unity_gauge_fills": training_data.get("unity_gauge_fills"),
      "unity_spirit_explosions": training_data.get("unity_spirit_explosions"),
      "filtered_out": False,
      "excluded_reason": None,
      "failure_bypassed_by_items": bool(training_data.get("failure_bypassed_by_items")),
    }

  return list(merged_trainings.values())
