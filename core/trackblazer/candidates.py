from __future__ import annotations

import copy
from typing import List

import core.config as config
import utils.constants as constants
from core.trackblazer.models import CandidateAction, DerivedTurnState, ObservedTurnState


def _append_candidate(candidates, *, node_id, kind, rationale, requirements, expected_warnings=None, source_facts=None):
  candidates.append(
    CandidateAction(
      node_id=node_id,
      kind=kind,
      priority_score=0.0,
      rationale=rationale,
      requirements=list(requirements or []),
      expected_warnings=list(expected_warnings or []),
      expected_followup_state={},
      source_facts=copy.deepcopy(source_facts or {}),
    )
  )


def _append_compatibility_candidates(candidates, observed_data, derived_data):
  # Transitional: these legacy-shaped reporting candidates preserve existing review/check output
  # during the M1-M3 plumbing slice. Planner-native nodes above remain the intended decision input.
  legacy_seed_metadata = observed_data.get("legacy_seed_metadata") or {}
  race_decision = legacy_seed_metadata.get("trackblazer_race_decision") or {}
  legacy_func = str(legacy_seed_metadata.get("func") or "")

  if legacy_func == "do_training":
    _append_candidate(
      candidates,
      node_id=f"compat:training:{legacy_seed_metadata.get('training_name') or 'unknown'}",
      kind="training",
      rationale="transitional legacy-shaped training candidate for dual-run parity",
      requirements=[],
      source_facts={"training_name": legacy_seed_metadata.get("training_name")},
    )
  elif legacy_func == "do_race":
    _append_candidate(
      candidates,
      node_id=f"compat:race:{legacy_seed_metadata.get('race_name') or 'any'}",
      kind="race",
      rationale="transitional legacy-shaped race candidate for dual-run parity",
      requirements=[],
      source_facts={"race_name": legacy_seed_metadata.get("race_name")},
    )
  elif legacy_func == "do_rest":
    _append_candidate(
      candidates,
      node_id="compat:rest",
      kind="rest",
      rationale="transitional legacy-shaped rest candidate for dual-run parity",
      requirements=[],
      source_facts={},
    )

  _append_candidate(
    candidates,
    node_id="compat:race_gate",
    kind="race_gate",
    rationale="transitional race-gate summary candidate for dual-run parity",
    requirements=[],
    expected_warnings=["consecutive_race_warning"] if any(getattr(entry, "kind", "") == "race" for entry in candidates) else [],
    source_facts={
      "race_opportunity": copy.deepcopy(derived_data.get("race_opportunity") or {}),
      "training_value_summary": copy.deepcopy(derived_data.get("training_value_summary") or {}),
    },
  )

  if legacy_seed_metadata.get("scheduled_race") and not legacy_seed_metadata.get("trackblazer_lobby_scheduled_race"):
    _append_candidate(
      candidates,
      node_id=f"compat:scheduled_race:{legacy_seed_metadata.get('race_name') or 'scheduled'}",
      kind="scheduled_race",
      rationale="transitional scheduled-race candidate for dual-run parity",
      requirements=[],
      expected_warnings=["consecutive_race_warning"],
      source_facts={"race_name": legacy_seed_metadata.get("race_name")},
    )

  if legacy_seed_metadata.get("is_race_day") and not legacy_seed_metadata.get("trackblazer_climax_race_day"):
    _append_candidate(
      candidates,
      node_id="compat:forced_race_day",
      kind="forced_race_day",
      rationale="transitional forced-race-day candidate for dual-run parity",
      requirements=[],
      expected_warnings=["consecutive_race_warning"],
      source_facts={"turn": observed_data.get("turn")},
    )

  if observed_data.get("trackblazer_climax_race_day") or legacy_seed_metadata.get("trackblazer_climax_race_day"):
    _append_candidate(
      candidates,
      node_id="compat:forced_climax_race",
      kind="forced_climax_race",
      rationale="transitional climax-race candidate for dual-run parity",
      requirements=[],
      source_facts={"trackblazer_climax_race_day": True},
    )

  if observed_data.get("race_mission_available") or legacy_seed_metadata.get("race_mission_available"):
    _append_candidate(
      candidates,
      node_id="compat:mission_race",
      kind="mission_race",
      rationale="transitional mission-race candidate for dual-run parity",
      requirements=[],
      source_facts={"race_mission_available": True},
    )

  if (
    legacy_func == "do_race"
    and legacy_seed_metadata.get("race_name")
    and legacy_seed_metadata.get("race_name") not in ("", "any")
    and not legacy_seed_metadata.get("scheduled_race")
    and not legacy_seed_metadata.get("trackblazer_lobby_scheduled_race")
    and not legacy_seed_metadata.get("trackblazer_climax_race_day")
  ):
    _append_candidate(
      candidates,
      node_id=f"compat:goal_race:{legacy_seed_metadata.get('race_name')}",
      kind="goal_race",
      rationale="transitional goal-race candidate for dual-run parity",
      requirements=[],
      source_facts={"race_name": legacy_seed_metadata.get("race_name")},
    )

  if legacy_seed_metadata.get("fallback_non_rival_race"):
    _append_candidate(
      candidates,
      node_id="compat:fallback_non_rival_race",
      kind="fallback_non_rival_race",
      rationale="transitional fallback non-rival race candidate for dual-run parity",
      requirements=[],
      source_facts={"reason": race_decision.get("reason")},
    )

  if legacy_func == "do_rest" and race_decision.get("prefer_rest_over_weak_training"):
    _append_candidate(
      candidates,
      node_id="compat:weak_training_rest",
      kind="weak_training_rest",
      rationale="transitional weak-training rest candidate for dual-run parity",
      requirements=[],
      source_facts={"reason": race_decision.get("reason")},
    )

  if legacy_seed_metadata.get("rest_promoted_to_training"):
    _append_candidate(
      candidates,
      node_id="compat:stat_focused_training_override",
      kind="stat_focused_training_override",
      rationale="transitional stat-focused training override candidate for dual-run parity",
      requirements=[],
      source_facts={"training_name": legacy_seed_metadata.get("training_name")},
    )


def planner_native_scheduled_race_name(observed_data) -> str:
  observed_data = observed_data if isinstance(observed_data, dict) else {}
  turn_label = str(observed_data.get("year") or "").strip()
  races_on_date = list(constants.RACES.get(turn_label, []) or [])
  if not races_on_date or not bool(getattr(config, "USE_RACE_SCHEDULE", False)):
    return ""

  scheduled_races_on_date = list(getattr(config, "RACE_SCHEDULE", {}).get(turn_label, []) or [])
  best_race_name = ""
  best_fans_gained = None
  for race in scheduled_races_on_date:
    if not isinstance(race, dict):
      continue
    race_name = str(race.get("name") or "").strip()
    if not race_name:
      continue
    try:
      fans_gained = int(race.get("fans_gained"))
    except (TypeError, ValueError):
      fans_gained = -1
    if best_fans_gained is None or fans_gained > best_fans_gained:
      best_race_name = race_name
      best_fans_gained = fans_gained
  return best_race_name


def planner_native_goal_race_name(observed_data) -> str:
  observed_data = observed_data if isinstance(observed_data, dict) else {}
  year = str(observed_data.get("year") or "")
  turn = observed_data.get("turn")
  criteria = str(observed_data.get("criteria") or "")
  keywords = ("fan", "Maiden", "Progress")

  try:
    numeric_turn = int(turn)
  except (TypeError, ValueError):
    numeric_turn = 0

  if year == "Junior Year Pre-Debut":
    return ""
  if numeric_turn > int(getattr(config, "RACE_TURN_THRESHOLD", 0) or 0) and "Maiden" not in criteria:
    return ""
  if not any(word in criteria for word in keywords):
    return ""
  if "Progress" not in criteria:
    return "any"
  if "G1" not in criteria and "GI" not in criteria:
    return "any"

  best_race_name = ""
  best_fans_gained = None
  for race in list(constants.RACES.get(year, []) or []):
    if not isinstance(race, dict):
      continue
    if str(race.get("grade") or "").upper() != "G1":
      continue
    race_name = str(race.get("name") or "").strip()
    if not race_name:
      continue
    try:
      fans_gained = int((race.get("fans") or {}).get("gained"))
    except (TypeError, ValueError):
      fans_gained = -1
    if best_fans_gained is None or fans_gained > best_fans_gained:
      best_race_name = race_name
      best_fans_gained = fans_gained
  return best_race_name or "any"


def enumerate_candidate_actions(observed: ObservedTurnState, derived: DerivedTurnState, policy: dict) -> List[CandidateAction]:
  observed_data = observed.to_dict()
  derived_data = derived.to_dict()
  missing_inputs = set(observed_data.get("missing_inputs") or [])
  race_opportunity = derived_data.get("race_opportunity") or {}
  training_values = list(derived_data.get("training_value") or [])
  candidates = []

  if "training" not in missing_inputs:
    for training in training_values:
      training_name = str(training.get("name") or "").strip()
      if not training_name:
        continue
      _append_candidate(
        candidates,
        node_id=f"train:{training_name}",
        kind="train",
        rationale=f"training scan produced {training_name} as a visible candidate",
        requirements=["training"],
        source_facts=training,
      )
      best_item_key = training.get("best_item_key")
      if training.get("item_assist_available") and best_item_key:
        _append_candidate(
          candidates,
          node_id=f"train:{training_name}+items:{best_item_key}",
          kind="train",
          rationale=f"{training_name} has planner-visible item assist via {best_item_key}",
          requirements=["training", "items"],
          source_facts={
            "training": copy.deepcopy(training),
            "item_key": best_item_key,
          },
        )

  _append_candidate(
    candidates,
    node_id="rest",
    kind="rest",
    rationale="baseline recovery candidate is always enumerable in the stub planner pass",
    requirements=["energy"],
    source_facts={"energy_class": derived_data.get("energy_class"), "energy_ratio": derived_data.get("energy_ratio")},
  )

  if str(observed_data.get("current_mood") or "").upper() in {"BAD", "AWFUL"}:
    _append_candidate(
      candidates,
      node_id="recreation",
      kind="recreation",
      rationale="low mood exposes recreation as a recovery option",
      requirements=["mood"],
      source_facts={"current_mood": observed_data.get("current_mood")},
    )

  if list(observed_data.get("status_effect_names") or []):
    _append_candidate(
      candidates,
      node_id="infirmary",
      kind="infirmary",
      rationale="status effects are present, so infirmary is available",
      requirements=["status"],
      source_facts={"status_effect_names": list(observed_data.get("status_effect_names") or [])},
    )

  if derived_data.get("skill_cadence_open"):
    _append_candidate(
      candidates,
      node_id="skill_purchase",
      kind="skill_purchase",
      rationale="skill cadence gate is open for this turn",
      requirements=["skills"],
      source_facts={
        "skill_cadence_open": True,
        "reason": derived_data.get("skill_cadence_reason") or observed_data.get("skill_purchase_check", {}).get("reason"),
        "current_sp": observed_data.get("current_stats", {}).get("sp"),
        "threshold_sp": observed_data.get("skill_purchase_check", {}).get("threshold_sp"),
        "scheduled_g1_race": bool(observed_data.get("skill_purchase_check", {}).get("scheduled_g1_race")),
      },
    )

  energy_ratio = derived_data.get("energy_ratio")
  rival_min_energy = float((policy or {}).get("rival_race_min_energy_ratio", 0.02) or 0.02)
  training_threshold = float((policy or {}).get("training_overrides_race_threshold", 30) or 30)
  best_training_score = None
  if training_values:
    best_training_score = max(
      (
        float(training.get("score") or 0.0)
        for training in training_values
        if isinstance(training, dict)
      ),
      default=None,
    )
  if race_opportunity.get("rival_visible") and isinstance(energy_ratio, (int, float)) and energy_ratio > rival_min_energy:
    _append_candidate(
      candidates,
      node_id="race:rival",
      kind="race",
      rationale="rival indicator is visible and energy clears the planner minimum",
      requirements=["race_opportunity", "energy"],
      expected_warnings=["consecutive_race_warning"],
      source_facts=race_opportunity,
    )
  elif (
    isinstance(energy_ratio, (int, float))
    and energy_ratio > rival_min_energy
    and not bool(derived_data.get("is_summer"))
    and list((constants.ALL_RACES or {}).get(observed_data.get("year"), []) or [])
    and (best_training_score is None or best_training_score < training_threshold)
  ):
    _append_candidate(
      candidates,
      node_id="race:fallback",
      kind="race",
      rationale="training board is below the race threshold and a normal race is available on this date",
      requirements=["race_opportunity", "energy", "training"],
      expected_warnings=["consecutive_race_warning"],
      source_facts={
        **copy.deepcopy(race_opportunity),
        "best_training_score": best_training_score,
        "training_overrides_race_threshold": training_threshold,
      },
    )

  if race_opportunity.get("lobby_scheduled"):
    scheduled_name = planner_native_scheduled_race_name(observed_data) or "scheduled"
    _append_candidate(
      candidates,
      node_id=f"race:scheduled:{scheduled_name}",
      kind="race",
      rationale="lobby scheduled-race indicator is present and planner-native schedule evaluation selected this race",
      requirements=["race_opportunity"],
      expected_warnings=["consecutive_race_warning"],
      source_facts={"race_name": scheduled_name, **copy.deepcopy(race_opportunity)},
    )

  if str(observed_data.get("turn") or "") == "Race Day":
    _append_candidate(
      candidates,
      node_id="race:race_day",
      kind="race",
      rationale="current turn is labeled Race Day",
      requirements=["turn_identity"],
      expected_warnings=["consecutive_race_warning"],
      source_facts={"turn": observed_data.get("turn")},
    )

  if race_opportunity.get("climax_locked"):
    _append_candidate(
      candidates,
      node_id="race:climax_locked",
      kind="race",
      rationale="climax lock is visible on the current lobby state",
      requirements=["race_opportunity"],
      source_facts=race_opportunity,
    )

  if bool(getattr(config, "DO_MISSION_RACES_IF_POSSIBLE", False)) and observed_data.get("race_mission_available"):
    _append_candidate(
      candidates,
      node_id="race:mission",
      kind="race",
      rationale="mission race is available and mission racing is enabled in planner policy",
      requirements=["race_opportunity"],
      expected_warnings=["consecutive_race_warning"],
      source_facts={"race_mission_available": True},
    )

  goal_race_name = planner_native_goal_race_name(observed_data)
  if goal_race_name:
    _append_candidate(
      candidates,
      node_id=f"race:goal:{goal_race_name}",
      kind="race",
      rationale="goal criteria require a planner-native race branch on this turn",
      requirements=["lookahead"],
      expected_warnings=["consecutive_race_warning"],
      source_facts={"race_name": goal_race_name, "criteria": observed_data.get("criteria")},
    )

  _append_compatibility_candidates(candidates, observed_data, derived_data)
  return candidates


def _legacy_enumerate_candidate_actions(observed: ObservedTurnState, derived: DerivedTurnState, policy: dict, state_obj=None, action=None, planner_state=None) -> List[CandidateAction]:
  # TODO remove after Milestone 4 callers stop passing legacy arguments.
  return enumerate_candidate_actions(observed, derived, policy)
