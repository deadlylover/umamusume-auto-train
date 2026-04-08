from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


_SKILL_SCAN_STATES = ("queued", "capturing", "processing", "ready", "stale", "failed")


@dataclass
class BackgroundSkillScanState:
  status: str = "stale"
  job_id: str = ""
  turn_key: str = ""
  observation_id: str = ""
  skill_context_key: str = ""
  captured_sp: Optional[int] = None
  captured_shortlist_hash: str = ""
  result_ref: str = ""
  reason: str = ""

  def __post_init__(self):
    if self.status not in _SKILL_SCAN_STATES:
      self.status = "stale"

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


@dataclass
class PlannerFreshness:
  turn_key: str = ""
  observation_id: str = ""
  state_key: str = ""
  action_key: str = ""
  skill_context_key: str = ""

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


@dataclass
class PlannerRuntimeState:
  turn_key: str = ""
  latest_observation_id: str = ""
  scan_cadence: Dict[str, Any] = field(default_factory=dict)
  pending_skill_scan: BackgroundSkillScanState = field(default_factory=BackgroundSkillScanState)
  fallback_count: int = 0
  last_fallback_reason: str = ""
  transition_breadcrumbs: List[Dict[str, Any]] = field(default_factory=list)

  def to_dict(self) -> Dict[str, Any]:
    payload = asdict(self)
    payload["pending_skill_scan"] = self.pending_skill_scan.to_dict()
    return payload


@dataclass
class ObservedTurnState:
  data: Dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> Dict[str, Any]:
    return dict(self.data)


@dataclass
class DerivedTurnState:
  data: Dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> Dict[str, Any]:
    return dict(self.data)


@dataclass
class CandidateAction:
  kind: str = ""
  priority_score: float = 0.0
  rationale: str = ""
  requirements: List[str] = field(default_factory=list)
  expected_warnings: List[str] = field(default_factory=list)
  expected_followup_state: Dict[str, Any] = field(default_factory=dict)
  source_facts: Dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


@dataclass
class ExecutionStep:
  step_type: str = ""
  intent: str = ""
  screen_preconditions: List[str] = field(default_factory=list)
  success_transition: str = ""
  failure_transition: str = ""
  retry_policy: Dict[str, Any] = field(default_factory=dict)
  planned_clicks: List[Dict[str, Any]] = field(default_factory=list)

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


@dataclass
class TransitionResult:
  status: str = ""
  popup_encountered: str = ""
  warning_outcome: str = ""
  details: Dict[str, Any] = field(default_factory=dict)

  def to_dict(self) -> Dict[str, Any]:
    return asdict(self)


@dataclass
class TurnPlan:
  version: int = 1
  decision_path: str = "legacy"
  freshness: PlannerFreshness = field(default_factory=PlannerFreshness)
  selected_candidate: Dict[str, Any] = field(default_factory=dict)
  candidate_ranking: List[Dict[str, Any]] = field(default_factory=list)
  shop_plan: Dict[str, Any] = field(default_factory=dict)
  item_plan: Dict[str, Any] = field(default_factory=dict)
  inventory_snapshot: Dict[str, Any] = field(default_factory=dict)
  timing: Dict[str, Any] = field(default_factory=dict)
  debug_summary: Dict[str, Any] = field(default_factory=dict)
  planner_metadata: Dict[str, Any] = field(default_factory=dict)
  legacy_shared_plan: Dict[str, Any] = field(default_factory=dict)
  step_sequence: List[ExecutionStep] = field(default_factory=list)

  def to_planned_actions(self) -> Dict[str, Any]:
    return dict(self.legacy_shared_plan)

  def to_snapshot(self) -> Dict[str, Any]:
    return {
      "version": self.version,
      "decision_path": self.decision_path,
      "freshness": self.freshness.to_dict(),
      "selected_candidate": dict(self.selected_candidate),
      "candidate_ranking": list(self.candidate_ranking),
      "shop_plan": dict(self.shop_plan),
      "item_plan": dict(self.item_plan),
      "inventory_snapshot": dict(self.inventory_snapshot),
      "timing": dict(self.timing),
      "debug_summary": dict(self.debug_summary),
      "planner_metadata": dict(self.planner_metadata),
      "legacy_shared_plan": self.to_planned_actions(),
      "step_sequence": [step.to_dict() for step in self.step_sequence],
    }
