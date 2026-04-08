from __future__ import annotations

import copy
from typing import List

from core.trackblazer.models import CandidateAction, DerivedTurnState, ObservedTurnState
from core.trackblazer_race_logic import evaluate_trackblazer_race


def _priority_score(*values):
  for value in values:
    try:
      return float(value)
    except (TypeError, ValueError):
      continue
  return 0.0


def _append_action_candidate(candidates, action_kind, selected_action, derived_data, rationale, source_facts=None):
  candidates.append(
    CandidateAction(
      kind=action_kind,
      priority_score=_priority_score(
        ((derived_data.get("usage_context_summary") or {}).get("training_score")),
        ((derived_data.get("training_value_summary") or {}).get("best_score")),
      ),
      rationale=rationale,
      requirements=[],
      expected_warnings=[],
      expected_followup_state={
        "reassess_after_item_use": bool(derived_data.get("reassess_after_item_use")),
      },
      source_facts=source_facts or {},
    )
  )


def _kind_from_action(selected_action):
  func = str(selected_action.get("func") or "")
  if func.startswith("do_"):
    return func[3:]
  return func or "unknown"


def _can_wrap_race_gate_directly(state_obj):
  if not isinstance(state_obj, dict):
    return False
  if "rival_indicator_detected" in state_obj:
    return True
  if state_obj.get("trackblazer_climax_locked_race"):
    return True
  if state_obj.get("turn") == "Race Day":
    return True
  return False


def _build_race_gate_candidate(race_decision, derived_data):
  if not isinstance(race_decision, dict) or not race_decision:
    return None
  expected_warnings = []
  if race_decision.get("should_race"):
    expected_warnings.append("consecutive_race_warning")
  return CandidateAction(
    kind="race_gate",
    priority_score=_priority_score(
      race_decision.get("training_score"),
      ((derived_data.get("usage_context_summary") or {}).get("training_score")),
    ),
    rationale=str(race_decision.get("reason") or "trackblazer race gate"),
    requirements=["cached_rival_indicator_or_mandatory_race_context"],
    expected_warnings=expected_warnings,
    expected_followup_state={
      "should_race": bool(race_decision.get("should_race")),
      "prefer_rival_race": bool(race_decision.get("prefer_rival_race")),
      "prefer_rest_over_weak_training": bool(race_decision.get("prefer_rest_over_weak_training")),
    },
    source_facts=dict(race_decision),
  )


def enumerate_candidate_actions(observed: ObservedTurnState, derived: DerivedTurnState, state_obj=None, action=None) -> List[CandidateAction]:
  observed_data = observed.to_dict()
  derived_data = derived.to_dict()
  selected_action = observed_data.get("selected_action") or {}
  race_decision = copy.deepcopy(selected_action.get("trackblazer_race_decision") or {})
  candidates = []
  action_kind = _kind_from_action(selected_action)

  if selected_action.get("func"):
    _append_action_candidate(
      candidates,
      action_kind,
      selected_action,
      derived_data,
      "legacy selected action",
      source_facts={
        "training_name": selected_action.get("training_name"),
        "race_name": selected_action.get("race_name"),
      },
    )

  if action_kind == "training" and (selected_action.get("training_data") or {}).get("failure_bypassed_by_items"):
    _append_action_candidate(
      candidates,
      "failure_bypass_training",
      selected_action,
      derived_data,
      "selected training remains viable because item policy can bypass failure risk",
      source_facts={
        "training_name": selected_action.get("training_name"),
        "failure": (selected_action.get("training_data") or {}).get("failure"),
      },
    )

  if action_kind == "training" and selected_action.get("rest_promoted_to_training"):
    _append_action_candidate(
      candidates,
      "stat_focused_training_override",
      selected_action,
      derived_data,
      "stat-focused Trackblazer scoring promoted a provisional rest turn back into training",
      source_facts={
        "training_name": selected_action.get("training_name"),
      },
    )

  if selected_action.get("is_race_day") and not selected_action.get("trackblazer_climax_race_day"):
    _append_action_candidate(
      candidates,
      "forced_race_day",
      selected_action,
      derived_data,
      "turn is on a forced race day in the legacy Trackblazer flow",
      source_facts={
        "is_race_day": True,
        "race_name": selected_action.get("race_name"),
      },
    )

  if observed_data.get("trackblazer_climax_race_day") or selected_action.get("trackblazer_climax_race_day"):
    _append_action_candidate(
      candidates,
      "forced_climax_race",
      selected_action,
      derived_data,
      "Trackblazer climax race-day UI replaces the normal lobby buttons",
      source_facts={
        "trackblazer_climax_race_day": True,
        "banner_detected": bool(observed_data.get("trackblazer_climax_race_day_banner")),
        "button_detected": bool(observed_data.get("trackblazer_climax_race_day_button")),
      },
    )

  if selected_action.get("scheduled_race") and not selected_action.get("trackblazer_lobby_scheduled_race"):
    _append_action_candidate(
      candidates,
      "scheduled_race",
      selected_action,
      derived_data,
      "scheduled race from the legacy race schedule branch is active",
      source_facts={
        "scheduled_race": True,
        "race_name": selected_action.get("race_name"),
      },
    )

  if observed_data.get("race_mission_available") or selected_action.get("race_mission_available"):
    _append_action_candidate(
      candidates,
      "mission_race",
      selected_action,
      derived_data,
      "mission race branch is available in the legacy Trackblazer flow",
      source_facts={
        "race_mission_available": True,
      },
    )

  if observed_data.get("trackblazer_lobby_scheduled_race") or selected_action.get("trackblazer_lobby_scheduled_race"):
    _append_action_candidate(
      candidates,
      "lobby_scheduled_race",
      selected_action,
      derived_data,
      "lobby scheduled-race indicator is present on the race button",
      source_facts={
        "trackblazer_lobby_scheduled_race": True,
      },
    )

  if (
    action_kind == "race"
    and selected_action.get("race_name")
    and selected_action.get("race_name") not in ("", "any")
    and not selected_action.get("scheduled_race")
    and not selected_action.get("trackblazer_lobby_scheduled_race")
    and not selected_action.get("trackblazer_climax_race_day")
  ):
    _append_action_candidate(
      candidates,
      "goal_race",
      selected_action,
      derived_data,
      "legacy flow selected a concrete race target",
      source_facts={
        "race_name": selected_action.get("race_name"),
      },
    )

  if action_kind == "race" and selected_action.get("fallback_non_rival_race"):
    _append_action_candidate(
      candidates,
      "fallback_non_rival_race",
      selected_action,
      derived_data,
      "race gate escalated a weak board into a non-rival fallback race",
      source_facts={
        "fallback_non_rival_race": True,
        "race_name": selected_action.get("race_name"),
        "reason": race_decision.get("reason"),
      },
    )

  if action_kind == "rest" and race_decision.get("prefer_rest_over_weak_training"):
    _append_action_candidate(
      candidates,
      "weak_training_rest",
      selected_action,
      derived_data,
      "race gate converted the selected turn into rest because the board was too weak to train or race",
      source_facts={
        "prefer_rest_over_weak_training": True,
        "reason": race_decision.get("reason"),
      },
    )

  race_decision = {}
  if _can_wrap_race_gate_directly(state_obj) and action is not None:
    try:
      race_decision = evaluate_trackblazer_race(state_obj, action) or {}
    except Exception:
      race_decision = copy.deepcopy(selected_action.get("trackblazer_race_decision") or {})
  else:
    race_decision = copy.deepcopy(selected_action.get("trackblazer_race_decision") or {})
    if not race_decision:
      race_decision = {
        "reason": "race gate not re-run in read-only planner mode because cached rival-indicator context is missing",
        "cached_only": True,
      }

  race_candidate = _build_race_gate_candidate(race_decision, derived_data)
  if race_candidate is not None:
    candidates.append(race_candidate)

  return candidates
