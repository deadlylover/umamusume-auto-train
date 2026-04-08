from __future__ import annotations

from typing import List

from core.trackblazer.models import CandidateAction, DerivedTurnState, ObservedTurnState


def enumerate_candidate_actions(observed: ObservedTurnState, derived: DerivedTurnState) -> List[CandidateAction]:
  observed_data = observed.to_dict()
  derived_data = derived.to_dict()
  selected_action = observed_data.get("selected_action") or {}
  race_decision = selected_action.get("trackblazer_race_decision") or {}
  candidates = []

  if selected_action.get("func"):
    candidates.append(
      CandidateAction(
        kind=str(selected_action.get("func")).replace("do_", ""),
        priority_score=float((derived_data.get("training_value_summary") or {}).get("best_score") or 0.0),
        rationale="legacy selected action",
        requirements=[],
        expected_warnings=[],
        expected_followup_state={
          "reassess_after_item_use": bool(derived_data.get("reassess_after_item_use")),
        },
        source_facts={
          "training_name": selected_action.get("training_name"),
          "race_name": selected_action.get("race_name"),
        },
      )
    )

  if race_decision:
    candidates.append(
      CandidateAction(
        kind="race",
        priority_score=float(race_decision.get("training_score") or 0.0),
        rationale=str(race_decision.get("reason") or "legacy race decision"),
        requirements=["operator_race_gate_enabled"],
        expected_warnings=["consecutive_race_warning"] if race_decision.get("should_race") else [],
        expected_followup_state={
          "prefer_rival_race": bool(race_decision.get("prefer_rival_race")),
          "should_race": bool(race_decision.get("should_race")),
        },
        source_facts=dict(race_decision),
      )
    )

  return candidates
