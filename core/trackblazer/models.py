from __future__ import annotations

import copy
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


def _format_number(value, digits=1):
  if value is None:
    return "?"
  try:
    number = float(value)
  except (TypeError, ValueError):
    return str(value)
  if digits <= 0:
    return str(int(round(number)))
  text = f"{number:.{digits}f}"
  return text.rstrip("0").rstrip(".")


def _format_ratio(left, right):
  return f"{_format_number(left)}/{_format_number(right)}"


def _safe_int(value, default_value=0):
  try:
    return int(value)
  except (TypeError, ValueError):
    return default_value


def _safe_float(value, default_value=None):
  try:
    return float(value)
  except (TypeError, ValueError):
    return default_value


def _humanize_item_name(value):
  text = str(value or "").replace("_", " ").strip()
  return text.title() if text else "Unknown"


def _stringify_list_item(value):
  if isinstance(value, dict):
    return str(value.get("name") or value.get("key") or value)
  return str(value)


def _visible_training_gains(gains):
  visible = {}
  for stat, value in (gains or {}).items():
    if stat == "sp":
      continue
    amount = _safe_int(value)
    if amount:
      visible[stat] = amount
  return visible


def _sum_visible_training_gains(gains):
  return sum(_visible_training_gains(gains).values())


def _format_training_gain_parts(training_name, gains):
  visible = _visible_training_gains(gains)
  ordered = []
  main_gain = visible.pop(training_name, 0)
  if main_gain:
    ordered.append(f"{training_name}+{main_gain}")
  for stat in sorted(visible):
    ordered.append(f"{stat}+{visible[stat]}")
  return ordered


def _format_current_stats_line(state_summary):
  current_stats = state_summary.get("current_stats")
  if not current_stats or not isinstance(current_stats, dict):
    return None
  stat_order = ["spd", "sta", "pwr", "guts", "wit", "sp"]
  parts = []
  for key in stat_order:
    val = current_stats.get(key)
    if val is None or val == -1:
      parts.append(f"{key}:?")
    else:
      parts.append(f"{key}:{val}")
  return "Stats: " + " | ".join(parts)


def _format_operator_race_gate_line(state_summary):
  gate = state_summary.get("operator_race_gate") or {}
  if not isinstance(gate, dict):
    return ""
  if not gate.get("enabled") and not gate.get("selected_race"):
    return ""

  parts = []
  if gate.get("enabled"):
    parts.append(f"Race Allowed: {'yes' if gate.get('race_allowed') else 'no'}")
    parts.append("source selector")
  else:
    parts.append("Race Allowed: legacy config")

  selected_race = gate.get("selected_race")
  if selected_race:
    parts.append(f"selected {selected_race}")
  return " | ".join(parts)


def _planner_path_label(snapshot_context, state_summary):
  planner_state = state_summary.get("trackblazer_planner_state") or {}
  turn_plan = planner_state.get("turn_plan") or {}
  decision_path = turn_plan.get("decision_path") or planner_state.get("decision_path") or "legacy"
  return str(decision_path or "legacy")


def _planner_comparison_line(snapshot_context):
  comparison = (snapshot_context or {}).get("planner_dual_run_comparison") or {}
  if not isinstance(comparison, dict) or comparison.get("match") is None:
    return ""
  return "Planner Comparison: match" if comparison.get("match") else "Planner Comparison: DIVERGED (see notes)"


def _format_selected_action_line(selected_action):
  action_name = selected_action.get("func") or "-"
  pre_action_items = selected_action.get("pre_action_item_use") or []
  pre_action_label = ""
  if pre_action_items:
    item_names = ", ".join(entry.get("name") or entry.get("key") or "item" for entry in pre_action_items)
    if selected_action.get("reassess_after_item_use"):
      pre_action_label = f"Action: use {item_names} -> recheck trainings"
    else:
      pre_action_label = f"Action: use {item_names} -> "
  if action_name == "do_training":
    training_name = selected_action.get("training_name") or "unknown"
    parts = [f"Action: train {training_name}"]
    score_tuple = selected_action.get("score_tuple") or ()
    if score_tuple:
      parts.append(f"score {_format_number(score_tuple[0], digits=3)}")
    gains = selected_action.get("stat_gains") or {}
    total_gain = _sum_visible_training_gains(gains)
    parts.extend(_format_training_gain_parts(training_name, gains))
    if total_gain:
      parts.append(f"total+{total_gain}")
    rainbows = selected_action.get("total_rainbow_friends")
    supports = selected_action.get("total_supports")
    if rainbows is not None:
      parts.append(f"rainbows {rainbows}")
    if supports is not None:
      parts.append(f"supports {supports}")
    failure = selected_action.get("failure")
    if failure is not None:
      parts.append(f"fail {_format_number(failure)}%")
    action_text = " | ".join(parts)
    return pre_action_label + action_text if pre_action_label and not selected_action.get("reassess_after_item_use") else (pre_action_label or action_text)
  if action_name == "do_race":
    race_name = selected_action.get("race_name") or "unspecified"
    action_text = f"Action: race {race_name}"
    return pre_action_label + action_text if pre_action_label and not selected_action.get("reassess_after_item_use") else (pre_action_label or action_text)
  action_text = f"Action: {action_name}"
  return pre_action_label + action_text if pre_action_label and not selected_action.get("reassess_after_item_use") else (pre_action_label or action_text)


def _format_rival_line(selected_action, state_summary):
  rival_scout = selected_action.get("rival_scout") or {}
  if isinstance(rival_scout, dict) and rival_scout:
    rival_found = rival_scout.get("rival_found")
    if rival_found is True:
      return "Race: rival race found"
    if rival_found is False:
      return "Race: rival race not found"
  rival_indicator = state_summary.get("rival_indicator_detected")
  if rival_indicator is True:
    return "Race: rival indicator detected"
  if rival_indicator is False:
    return "Race: rival indicator not detected"
  mission = state_summary.get("race_mission_available")
  if mission is not None:
    return f"Race mission available: {mission}"
  return ""


def _format_race_check_line(planned):
  race_check = planned.get("race_check") or {}
  race_scout = planned.get("race_scout") or {}
  if not race_check and not race_scout:
    return ""

  parts = []
  if race_check:
    indicator = race_check.get("rival_indicator_detected")
    if indicator is True:
      parts.append("indicator yes")
    elif indicator is False:
      parts.append("indicator no")
    if race_check.get("scheduled_race"):
      parts.append("scheduled yes")
      scheduled_source = race_check.get("scheduled_race_source")
      if scheduled_source:
        parts.append(f"source {scheduled_source}")
    method = race_check.get("method")
    if method:
      parts.append(f"check {method}")
  if race_scout:
    if race_scout.get("executed"):
      rival_found = race_scout.get("rival_found")
      if rival_found is True:
        parts.append("scout found")
      elif rival_found is False:
        parts.append("scout none")
      else:
        parts.append("scout ran")
    else:
      parts.append("scout deferred")
  return f"Race Flow: {' | '.join(parts)}" if parts else ""


def _format_skill_check_lines(state_summary):
  skill_check = state_summary.get("skill_purchase_check") or {}
  if not isinstance(skill_check, dict) or not skill_check:
    return []

  lines = []
  skill_flow = state_summary.get("skill_purchase_flow") or {}
  skill_scan = state_summary.get("skill_purchase_scan") or {}
  skill_plan = state_summary.get("skill_purchase_plan") or {}
  budget_plan = skill_plan.get("budget_plan") or {}
  plan_by_target = budget_plan.get("plan_by_target") or {}
  current_sp = skill_check.get("current_sp")
  threshold_sp = skill_check.get("threshold_sp")
  auto_buy_enabled = skill_check.get("auto_buy_skill_enabled")
  last_review = skill_check.get("last_skill_purchase_check_action_count")
  last_purchase = skill_check.get("last_skill_purchase_action_count")
  last_selected_race_review = skill_check.get("last_selected_race_skill_purchase_check_action_count")
  recheck_turns = skill_check.get("skill_recheck_turns")
  selected_race_recheck_turns = skill_check.get("skill_selected_race_recheck_turns")
  scheduled_g1 = bool(skill_check.get("scheduled_g1_race"))
  reason = skill_check.get("reason") or ""

  if current_sp is not None or threshold_sp is not None:
    lines.append(
      f"  SP: {current_sp if current_sp is not None else '?'}"
      f" / {threshold_sp if threshold_sp is not None else '?'}"
    )
  if auto_buy_enabled is not None:
    lines.append(f"  Auto-buy skill: {'yes' if auto_buy_enabled else 'no'}")
  if isinstance(skill_flow, dict) and skill_flow:
    if skill_flow.get("scanned"):
      lines.append("  Scan: scanned")
    elif skill_flow.get("skipped"):
      lines.append(f"  Scan: skipped ({skill_flow.get('reason') or 'unknown'})")
    elif skill_flow.get("reason"):
      lines.append(f"  Scan: {skill_flow.get('reason')}")
  target_results = list(skill_scan.get("target_results") or [])
  if target_results:
    detected = []
    actionable = []
    unavailable = []
    for entry in target_results:
      candidate = entry.get("candidate") or {}
      target_skill = entry.get("target_skill") or "unknown"
      match_name = candidate.get("match_name") or target_skill
      budget_entry = plan_by_target.get(target_skill) or {}
      increment_click_result = entry.get("increment_click_result") or {}
      if candidate:
        detected.append(match_name)
        if increment_click_result.get("target"):
          actionable.append(match_name)
        elif budget_entry.get("available"):
          actionable.append(match_name)
        else:
          unavailable.append(match_name)
      else:
        unavailable.append(target_skill)
    if detected:
      lines.append(f"  Detected: {', '.join(detected)}")
    if actionable:
      lines.append(f"  Actionable: {', '.join(actionable)}")
    if unavailable:
      lines.append(f"  Non-actionable: {', '.join(unavailable)}")
  selected_targets = list(budget_plan.get("selected_targets") or [])
  if selected_targets:
    selected_entries = []
    for skill_name in selected_targets:
      plan_entry = plan_by_target.get(skill_name) or {}
      estimated_cost = plan_entry.get("estimated_cost")
      cost_suffix = f" ({estimated_cost})" if estimated_cost is not None else ""
      selected_entries.append(f"{skill_name}{cost_suffix}")
    lines.append(f"  Queued: {', '.join(selected_entries)}")
  if last_review is not None or last_purchase is not None:
    lines.append(
      f"  Last: review {last_review if last_review is not None else '-'}"
      f" | purchase {last_purchase if last_purchase is not None else '-'}"
    )
  if recheck_turns is not None:
    lines.append(f"  Cooldown: {recheck_turns} turns")
  if selected_race_recheck_turns is not None:
    lines.append(
      f"  Selected race cooldown: {selected_race_recheck_turns} turns"
      f" | last {last_selected_race_review if last_selected_race_review is not None else '-'}"
    )
  lines.append(f"  Selected race bypass: {'yes' if scheduled_g1 else 'no'}")
  if reason:
    lines.append(f"  Status: {reason}")
  return lines


def _format_trackblazer_race_lines(selected_action):
  decision = selected_action.get("trackblazer_race_decision") or {}
  lookahead = selected_action.get("trackblazer_race_lookahead") or {}
  lines = []
  if isinstance(decision, dict) and decision:
    if decision.get("prefer_rest_over_weak_training"):
      outcome = "rest"
    elif decision.get("should_race"):
      outcome = "fallback_race" if decision.get("fallback_non_rival_race") else "race"
    else:
      outcome = "train"
    parts = [f"Race Gate: {outcome}"]
    target = decision.get("race_tier_target")
    if target:
      parts.append(f"target {target}")
    race_name = decision.get("race_name")
    if race_name:
      parts.append(f"race {race_name}")
    training_total = decision.get("training_total_stats")
    if training_total is not None:
      parts.append(f"training_total {training_total}")
    training_score = decision.get("training_score")
    if training_score is not None:
      parts.append(f"training_score {training_score}")
    if decision.get("rival_indicator"):
      parts.append("rival yes")
    if decision.get("is_summer"):
      parts.append("summer yes")

    lines.append(" | ".join(parts))
    reason = decision.get("reason")
    if reason:
      lines.append(f"Race Gate Reason: {reason}")
  if isinstance(lookahead, dict) and lookahead.get("conserve"):
    parts = [
      "Race Lookahead: conserve",
      f"target {lookahead.get('safe_energy_target', '?')}/{lookahead.get('max_energy', '?')}",
    ]
    energy_item_key = lookahead.get("energy_item_key")
    if energy_item_key:
      parts.append(f"item {energy_item_key}")
    lines.append(" | ".join(parts))
    reason = lookahead.get("reason")
    if reason:
      lines.append(f"Race Lookahead Reason: {reason}")
  return lines


def _format_trackblazer_race_entry_lines(planned):
  entry_gate = planned.get("race_entry_gate") or {}
  if not isinstance(entry_gate, dict) or not entry_gate:
    return []

  parts = ["Race Entry Gate: lobby -> race list"]
  expected_branch = entry_gate.get("expected_branch")
  if expected_branch:
    parts.append(f"expected {expected_branch}")
  lines = [" | ".join(parts)]

  meaning = entry_gate.get("warning_meaning")
  if meaning:
    lines.append(f"Race Warning: {meaning}")
  warning_ok_template = entry_gate.get("consecutive_warning_ok_template")
  if warning_ok_template:
    lines.append(f"Race Warning OK Template: {warning_ok_template}")
  if entry_gate.get("force_accept_warning"):
    lines.append("Race Warning Policy: scheduled race override forces OK")

  ok_action = entry_gate.get("ok_action")
  cancel_action = entry_gate.get("cancel_action")
  if ok_action or cancel_action:
    lines.append(f"Race Warning Buttons: ok={ok_action or '-'} | cancel={cancel_action or '-'}")
  return lines


def _format_training_lines(ranked_trainings, selected_action):
  if not ranked_trainings:
    return []
  entries = []
  for entry in ranked_trainings:
    if not isinstance(entry, dict):
      continue
    score_tuple = entry.get("score_tuple") or ()
    score_value = score_tuple[0] if score_tuple else None
    gains = entry.get("stat_gains") or {}
    total_gain = _sum_visible_training_gains(gains)
    entries.append(
      {
        "name": entry.get("name") or "?",
        "score": _safe_float(score_value),
        "failure": entry.get("failure"),
        "supports": entry.get("total_supports"),
        "rainbows": entry.get("total_rainbow_friends"),
        "total_gain": total_gain,
        "stat_gains": gains,
        "filtered_out": bool(entry.get("filtered_out")),
        "excluded_reason": entry.get("excluded_reason"),
        "max_allowed_failure": entry.get("max_allowed_failure"),
        "failure_bypassed_by_items": bool(entry.get("failure_bypassed_by_items")),
      }
    )
  entries.sort(
    key=lambda item: (
      1 if not item["filtered_out"] else 0,
      item["score"] if item["score"] is not None else float("-inf"),
      item["total_gain"],
    ),
    reverse=True,
  )
  selected_name = selected_action.get("training_name")
  lines = []
  for idx, entry in enumerate(entries, start=1):
    marker = "*" if entry["name"] == selected_name else " "
    parts = [f"  {marker} {idx}. {entry['name']}"]
    if entry["score"] is not None:
      parts.append(f"score {_format_number(entry['score'], digits=3)}")
    parts.extend(_format_training_gain_parts(entry["name"], entry.get("stat_gains") or {}))
    if entry["total_gain"]:
      parts.append(f"total+{entry['total_gain']}")
    if entry.get("rainbows") is not None:
      parts.append(f"rainbows {entry['rainbows']}")
    if entry.get("supports") is not None:
      parts.append(f"supports {entry['supports']}")
    if entry.get("failure") is not None:
      parts.append(f"fail {_format_number(entry['failure'])}%")
    if entry.get("failure_bypassed_by_items"):
      parts.append("items clear")
    if entry.get("filtered_out") and entry.get("max_allowed_failure") is not None:
      parts.append(f"limit {_format_number(entry['max_allowed_failure'])}%")
    if entry.get("excluded_reason"):
      parts.append(entry["excluded_reason"])
    lines.append(" | ".join(parts))
  return lines


def _collect_held_quantities(planned):
  held = {}
  for entry in (planned.get("would_use") or []) + (planned.get("deferred_use") or []):
    if not isinstance(entry, dict):
      continue
    item_key = entry.get("key")
    if item_key and entry.get("held_quantity") is not None:
      held[item_key] = entry.get("held_quantity")
  if held:
    return held
  return dict((planned.get("inventory_scan") or {}).get("held_quantities") or {})


def _format_inventory_items(item_keys, held_quantities):
  rendered = []
  for item_key in item_keys:
    count = held_quantities.get(item_key)
    label = _humanize_item_name(item_key)
    if count not in (None, ""):
      label += f" x{count}"
    rendered.append(label)
  return ", ".join(rendered)


def _format_candidate_list(items, include_reason=False):
  rendered = []
  for item in items:
    if not isinstance(item, dict):
      continue
    label = item.get("name") or _humanize_item_name(item.get("key"))
    if include_reason and item.get("reason"):
      label += f" ({item['reason']})"
    rendered.append(label)
  return ", ".join(rendered) if rendered else "none"


def _format_inventory_lines(planned):
  inventory_scan = planned.get("inventory_scan") or {}
  lines = []
  status = inventory_scan.get("status") or "unknown"
  button_visible = inventory_scan.get("button_visible")
  status_line = f"  Scan: {status}"
  if button_visible is not None:
    status_line += f" | button visible: {button_visible}"
  lines.append(status_line)

  detected = list(inventory_scan.get("items_detected") or [])
  held = _collect_held_quantities(planned)
  if not held:
    held = dict(inventory_scan.get("held_quantities") or {})
  if not detected and held:
    detected = list(held.keys())
  if detected:
    lines.append(f"  Held: {_format_inventory_items(detected, held)}")

  would_use = planned.get("would_use") or []
  if would_use:
    lines.append(f"  Use now: {_format_candidate_list(would_use)}")
  else:
    lines.append("  Use now: none")

  deferred = planned.get("deferred_use") or []
  if deferred:
    lines.append(f"  Deferred: {_format_candidate_list(deferred, include_reason=True)}")
  return lines


def _format_shop_buy_list(items):
  rendered = []
  for item in items:
    if not isinstance(item, dict):
      continue
    label = item.get("name") or _humanize_item_name(item.get("key"))
    cost = item.get("cost")
    if cost is not None:
      label += f" ({cost})"
    rendered.append(label)
  return ", ".join(rendered) if rendered else "none"


def _format_shop_lines(planned, state_summary):
  shop_scan = planned.get("shop_scan") or {}
  lines = []
  status = shop_scan.get("status") or "unknown"
  shop_coins = shop_scan.get("shop_coins")
  status_line = f"  Scan: {status}"
  if shop_coins not in (None, ""):
    status_line += f" | coins: {shop_coins}"
  lines.append(status_line)

  would_buy = planned.get("would_buy") or []
  if would_buy:
    lines.append(f"  Buy: {_format_shop_buy_list(would_buy)}")
  else:
    lines.append("  Buy: none")

  priority_preview = state_summary.get("trackblazer_shop_priority_preview") or []
  if priority_preview:
    preview_names = [item.get("name") for item in priority_preview if item.get("name")]
    if preview_names:
      lines.append(f"  Priorities: {', '.join(preview_names)}")
  return lines


def _format_compact_timing_lines(state_summary):
  lines = []
  inventory_flow = (
    state_summary.get("trackblazer_inventory_pre_shop_flow")
    or state_summary.get("trackblazer_inventory_flow")
    or {}
  )
  shop_flow = state_summary.get("trackblazer_shop_flow") or {}
  skill_flow = state_summary.get("skill_purchase_flow") or {}
  for label, flow in (
    ("inventory", inventory_flow),
    ("shop", shop_flow),
    ("skill", skill_flow),
  ):
    if not isinstance(flow, dict):
      continue
    total = flow.get("timing_total")
    if total is None:
      continue
    parts = [f"  {label}: {_format_number(total, digits=3)}s"]
    for key in ("timing_open", "timing_scan", "timing_close"):
      value = flow.get(key)
      if value is not None:
        parts.append(f"{key.replace('timing_', '')} {_format_number(value, digits=3)}s")
    lines.append(" | ".join(parts))
  return lines


def _format_short_mapping(payload):
  lines = []
  for key, value in payload.items():
    if value in (None, "", [], {}):
      continue
    if isinstance(value, list):
      rendered = ", ".join(_stringify_list_item(item) for item in value)
      lines.append(f"{key}: {rendered}")
    elif isinstance(value, dict):
      parts = []
      for sub_key, sub_value in value.items():
        if sub_value in (None, "", [], {}):
          continue
        parts.append(f"{sub_key}={sub_value}")
      if parts:
        lines.append(f"{key}: {'; '.join(parts)}")
    else:
      lines.append(f"{key}: {value}")
  return lines


def _format_short_list(payload):
  lines = []
  for item in payload:
    if isinstance(item, dict):
      name = item.get("name") or item.get("key") or "item"
      entry = name
      details = []
      priority = item.get("priority")
      if priority:
        details.append(f"policy={priority}")
      target_training = item.get("target_training")
      if target_training:
        details.append(f"target={target_training}")
      usage_group = item.get("usage_group")
      if usage_group:
        details.append(f"group={usage_group}")
      held_quantity = item.get("held_quantity")
      max_quantity = item.get("max_quantity")
      if held_quantity is not None and max_quantity not in (None, "", 0):
        details.append(f"hold {held_quantity}/{max_quantity}")
      reserve_quantity = item.get("reserve_quantity")
      if reserve_quantity not in (None, "", 0):
        details.append(f"reserve {reserve_quantity}")
      cost = item.get("cost")
      if cost not in (None, ""):
        details.append(f"cost {cost}")
      if not details:
        reason = item.get("reason")
        if reason:
          details.append(reason)
      if details:
        entry += f" | {'; '.join(str(detail) for detail in details)}"
      lines.append(entry)
    else:
      lines.append(str(item))
  return lines


def _format_planned_action_sections(planned, state_summary):
  lines = []
  for section_name, payload in (
    ("Race Check", planned.get("race_check") or {}),
    ("Race Decision", planned.get("race_decision") or {}),
    ("Race Entry Gate", planned.get("race_entry_gate") or {}),
    ("Skill Check", state_summary.get("skill_purchase_check") or {}),
    ("Race Scout", planned.get("race_scout") or {}),
    ("Inventory Scan", planned.get("inventory_scan") or {}),
    ("Would Use", planned.get("would_use") or []),
    ("Deferred Use", planned.get("deferred_use") or []),
    ("Shop Scan", planned.get("shop_scan") or {}),
    ("Would Buy", planned.get("would_buy") or []),
  ):
    lines.append(f"  {section_name}:")
    if isinstance(payload, dict):
      summary = _format_short_mapping(payload)
      if section_name == "Skill Check":
        summary = _format_skill_check_lines(state_summary)
      if summary:
        lines.extend([f"    {line}" for line in summary])
      else:
        lines.append("    none")
    elif isinstance(payload, list):
      if payload:
        for line in _format_short_list(payload):
          lines.append(f"    {line}")
      else:
        lines.append("    none")
  return lines


def _format_planned_clicks(planned_clicks):
  lines = []
  for idx, click in enumerate(planned_clicks or [], start=1):
    if not isinstance(click, dict):
      continue
    line = f"  {idx}. {click.get('label') or 'click'}"
    note_parts = []
    if click.get("template"):
      note_parts.append(click["template"])
    if click.get("target"):
      note_parts.append(f"target={click['target']}")
    if click.get("region_key"):
      note_parts.append(f"region={click['region_key']}")
    if click.get("note"):
      note_parts.append(click["note"])
    if note_parts:
      line += f" | {' | '.join(note_parts)}"
    lines.append(line)
  return lines


def _review_summary_lines(snapshot_context, planned_actions, include_prompt=False):
  snapshot_context = snapshot_context if isinstance(snapshot_context, dict) else {}
  planned_actions = planned_actions if isinstance(planned_actions, dict) else {}
  state_summary = snapshot_context.get("state_summary") or {}
  selected_action = snapshot_context.get("selected_action") or {}
  ranked_trainings = snapshot_context.get("ranked_trainings") or []
  lines = []
  turn_label = snapshot_context.get("turn_label") or "?"

  if include_prompt:
    lines.append("Compact Turn Summary")
    lines.append("Use this for quick back-and-forth turn review.")
    lines.append("")

  lines.append(
    "Turn: "
    f"{turn_label}"
    f" | Scenario: {snapshot_context.get('scenario_name') or '-'}"
    f" | Intent: {snapshot_context.get('execution_intent') or '-'}"
    f" | Path: {_planner_path_label(snapshot_context, state_summary)}"
  )
  comparison_line = _planner_comparison_line(snapshot_context)
  if comparison_line:
    lines.append(comparison_line)
  lines.append(
    "State: "
    f"mood {state_summary.get('current_mood') or '-'}"
    f" | energy {_format_ratio(state_summary.get('energy_level'), state_summary.get('max_energy'))}"
    f" | backend {state_summary.get('control_backend') or '-'}"
  )

  stats_line = _format_current_stats_line(state_summary)
  if stats_line:
    lines.append(stats_line)

  criteria = state_summary.get("criteria")
  if criteria:
    lines.append(f"Criteria: {criteria}")

  gate_line = _format_operator_race_gate_line(state_summary)
  if gate_line:
    lines.append(gate_line)

  lines.append(_format_selected_action_line(selected_action))

  rival_line = _format_rival_line(selected_action, state_summary)
  if rival_line:
    lines.append(rival_line)

  race_check_line = _format_race_check_line(planned_actions)
  if race_check_line:
    lines.append(race_check_line)

  race_gate_lines = _format_trackblazer_race_lines(selected_action)
  if race_gate_lines:
    lines.extend(race_gate_lines)

  race_entry_lines = _format_trackblazer_race_entry_lines(planned_actions)
  if race_entry_lines:
    lines.extend(race_entry_lines)

  skill_check_lines = _format_skill_check_lines(state_summary)
  if skill_check_lines:
    lines.append("")
    lines.append("Skill Check")
    lines.extend(skill_check_lines)

  training_lines = _format_training_lines(ranked_trainings, selected_action)
  if training_lines:
    lines.append("")
    lines.append("Trainings")
    lines.extend(training_lines)

  inventory_lines = _format_inventory_lines(planned_actions)
  if inventory_lines:
    lines.append("")
    lines.append("Inventory")
    lines.extend(inventory_lines)

  shop_lines = _format_shop_lines(planned_actions, state_summary)
  if shop_lines:
    lines.append("")
    lines.append("Shop")
    lines.extend(shop_lines)

  timing_lines = _format_compact_timing_lines(state_summary)
  if timing_lines:
    lines.append("")
    lines.append("Timing")
    lines.extend(timing_lines)

  notes = snapshot_context.get("reasoning_notes")
  if notes:
    lines.append("")
    lines.append(f"Notes: {notes}")
  return lines


def render_compact_summary(snapshot_context, planned_actions, include_prompt=True):
  lines = _review_summary_lines(
    snapshot_context,
    planned_actions,
    include_prompt=include_prompt,
  )
  return "\n".join(lines).strip()


def build_quick_bar_payload(snapshot_context, planned_actions):
  snapshot_context = snapshot_context if isinstance(snapshot_context, dict) else {}
  planned_actions = planned_actions if isinstance(planned_actions, dict) else {}

  planned_clicks = snapshot_context.get("planned_clicks") or []
  click_labels = []
  for click in planned_clicks:
    if not isinstance(click, dict):
      continue
    click_labels.append(click.get("label") or "click")

  would_use = list(planned_actions.get("would_use") or [])
  would_buy = list(planned_actions.get("would_buy") or [])
  return {
    "planned_click_labels": click_labels,
    "planned_clicks_text": " \u2192 ".join(click_labels) if click_labels else "-",
    "would_use": copy.deepcopy(would_use),
    "would_use_text": _format_candidate_list(would_use) if would_use else "-",
    "would_buy": copy.deepcopy(would_buy),
    "would_buy_text": _format_shop_buy_list(would_buy) if would_buy else "-",
  }


def render_turn_discussion(snapshot_context, planned_actions):
  snapshot_context = snapshot_context if isinstance(snapshot_context, dict) else {}
  planned_actions = planned_actions if isinstance(planned_actions, dict) else {}
  state_summary = snapshot_context.get("state_summary") or {}
  lines = []

  turn_label = snapshot_context.get("turn_label") or "?"
  year_label = state_summary.get("year") or turn_label
  lines.append("Turn Discussion")
  lines.append(f"Paste this back and we can discuss this turn. Year: {year_label}.")
  lines.append("")

  lines.extend(_review_summary_lines(snapshot_context, planned_actions))

  lines.append("")
  lines.append("Planned Actions")
  if planned_actions:
    lines.extend(_format_planned_action_sections(planned_actions, state_summary))
  else:
    lines.append("  No planned actions yet")

  planned_clicks = snapshot_context.get("planned_clicks") or []
  if planned_clicks:
    lines.append("")
    lines.append("Planned Clicks")
    lines.extend(_format_planned_clicks(planned_clicks))
  return "\n".join(lines).strip()


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
  review_context: Dict[str, Any] = field(default_factory=dict)
  legacy_shared_plan: Dict[str, Any] = field(default_factory=dict)
  step_sequence: List[ExecutionStep] = field(default_factory=list)

  def to_planned_actions(self) -> Dict[str, Any]:
    planned = dict(self.legacy_shared_plan)

    inventory_scan = dict(planned.get("inventory_scan") or {})
    inventory_scan.update(dict((self.inventory_snapshot or {}).get("scan") or {}))
    if inventory_scan:
      planned["inventory_scan"] = inventory_scan

    shop_scan = dict(planned.get("shop_scan") or {})
    shop_scan.update(dict((self.shop_plan or {}).get("scan") or {}))
    if shop_scan:
      planned["shop_scan"] = shop_scan

    planner_would_buy = list((self.shop_plan or {}).get("would_buy") or [])
    if planner_would_buy:
      planned["would_buy"] = planner_would_buy

    planner_would_use = list((self.item_plan or {}).get("pre_action_items") or [])
    if planner_would_use:
      planned["would_use"] = planner_would_use

    planner_deferred_use = list((self.item_plan or {}).get("deferred_use") or [])
    if planner_deferred_use or "deferred_use" not in planned:
      planned["deferred_use"] = planner_deferred_use

    item_context = dict((self.item_plan or {}).get("context") or {})
    if item_context:
      planned["would_use_context"] = item_context

    return planned

  @classmethod
  def from_snapshot(cls, snapshot: Optional[Dict[str, Any]] = None) -> "TurnPlan":
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    freshness = PlannerFreshness(**dict(snapshot.get("freshness") or {}))
    step_sequence = [
      ExecutionStep(**dict(step or {}))
      for step in list(snapshot.get("step_sequence") or [])
      if isinstance(step, dict)
    ]
    return cls(
      version=int(snapshot.get("version") or 1),
      decision_path=str(snapshot.get("decision_path") or "legacy"),
      freshness=freshness,
      selected_candidate=dict(snapshot.get("selected_candidate") or {}),
      candidate_ranking=list(snapshot.get("candidate_ranking") or []),
      shop_plan=dict(snapshot.get("shop_plan") or {}),
      item_plan=dict(snapshot.get("item_plan") or {}),
      inventory_snapshot=dict(snapshot.get("inventory_snapshot") or {}),
      timing=dict(snapshot.get("timing") or {}),
      debug_summary=dict(snapshot.get("debug_summary") or {}),
      planner_metadata=dict(snapshot.get("planner_metadata") or {}),
      review_context=dict(snapshot.get("review_context") or {}),
      legacy_shared_plan=dict(snapshot.get("legacy_shared_plan") or {}),
      step_sequence=step_sequence,
    )

  def to_turn_discussion(self, snapshot_context: Optional[Dict[str, Any]] = None) -> str:
    merged_context = self._merged_review_context(snapshot_context)
    return render_turn_discussion(merged_context, self.to_planned_actions())

  def to_compact_summary(self, snapshot_context: Optional[Dict[str, Any]] = None, include_prompt: bool = True) -> str:
    merged_context = self._merged_review_context(snapshot_context)
    return render_compact_summary(
      merged_context,
      self.to_planned_actions(),
      include_prompt=include_prompt,
    )

  def to_quick_bar(self, snapshot_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    merged_context = self._merged_review_context(snapshot_context)
    return build_quick_bar_payload(merged_context, self.to_planned_actions())

  def _merged_review_context(self, snapshot_context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    snapshot_context = snapshot_context if isinstance(snapshot_context, dict) else {}
    merged_context = dict(snapshot_context)
    review_context = dict(self.review_context or {})
    for key in ("selected_action", "ranked_trainings", "reasoning_notes", "planned_clicks"):
      value = review_context.get(key)
      if value in (None, {}, []):
        continue
      merged_context[key] = copy.deepcopy(value)
    return merged_context

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
      "review_context": dict(self.review_context),
      "legacy_shared_plan": self.to_planned_actions(),
      "step_sequence": [step.to_dict() for step in self.step_sequence],
    }
