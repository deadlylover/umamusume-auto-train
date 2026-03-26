# PRD: Prevent Duplicate Same-Turn Training Scans

## Problem

In `check_only`, the bot can perform a full training scan, return to the lobby, and then immediately perform the same training scan again on the same turn.

Observed sequence:

1. Stable lobby detected.
2. `collect_training_state()` opens training and scans all five trainings.
3. Training screen closes.
4. Stable lobby is detected again a couple of seconds later.
5. `collect_training_state()` runs again on the same turn.

This is expensive, increases OCR noise, and makes the operator history harder to trust because the restart reason is not surfaced.

## Likely Root Cause

There is only one `collect_training_state()` call site in the main loop:

- [core/skeleton.py](/Users/loli/umaautomac/umamusume-auto-train/core/skeleton.py#L3634) calls training scan once per loop pass.

So a duplicate training scan means the bot re-entered the top of `career_lobby()` on the same turn.

For the reported case, these branches are the relevant ones:

- Skill review is not the cause here.
  The live config has `skill.is_auto_buy_skill = false`, so `maybe_review_skill_purchase()` should short-circuit before any review wait.
- `check_only` preview of the final action is also unlikely to be the cause of the immediate re-scan in this report.
  With `execution_mode = "auto"`, the run does not pause in `review_action_before_execution()`; a duplicate scan only happens if the loop reaches a retry path before final commit.

The strongest candidate is late state validation:

- [core/state.py](/Users/loli/umaautomac/umamusume-auto-train/core/state.py#L399) collects the main state first.
- [core/strategies.py](/Users/loli/umaautomac/umamusume-auto-train/core/strategies.py#L32) validates that state only inside `Strategy.decide()`.
- [core/strategies.py](/Users/loli/umaautomac/umamusume-auto-train/core/strategies.py#L518) marks the state invalid when `year`, `turn`, `criteria`, or all stats are unreadable.
- [core/skeleton.py](/Users/loli/umaautomac/umamusume-auto-train/core/skeleton.py#L3776) then logs "State invalid, retrying..." and falls through to the next lobby-loop pass.

That means a transient OCR miss in `collect_main_state()` can force a full retry only after the bot already paid for an expensive training scan.

## Why The Current Runtime Hides It

The current debug history clearly shows:

- stable lobby detection
- training button open/close
- per-training scan entries

But it does not push an explicit debug-history event for:

- main-state validation failure
- retry reason
- same-turn loop restart reason

So from the operator view the duplicate scan looks mysterious even when the loop is behaving as coded.

## Goal

Do not perform training scan when the main state is already known to be invalid.

If the run retries the same turn, make the retry reason explicit in both:

- operator snapshot
- debug history

## Non-Goals

- Reworking training scoring
- Reworking Trackblazer item policy
- Changing intentional re-scan flows after real state mutation, such as reassess-after-item-use

## Proposed Fix

### 1. Move state validation before training scan

Extract the current validation rules into a shared helper and run them immediately after `collect_main_state()`.

Suggested shape:

- add a reusable validator in `core/strategies.py` or a new small runtime helper
- call it in `career_lobby()` right after `state_obj = collect_main_state()`
- if invalid:
  - update the operator snapshot with a specific retry message
  - attach OCR debug for `turn`, `year`, `criteria`, `energy`, and stat OCR where available
  - push a debug-history event like:
    - `event: "state_validation"`
    - `result: "invalid_retry"`
    - `context: "pre_training_scan"`
    - `reasons: [...]`
  - `continue` before `collect_training_state()`

This keeps the retry cheap and prevents the double training scan in the most likely failure mode.

### 2. Preserve current strategy behavior, but stop using it as the first invalid-state gate

`Strategy.decide()` should still defend against invalid state, but that should be the fallback guard, not the first place validation happens.

Desired outcome:

- normal path: invalid state is rejected before training scan
- defensive path: if an invalid state still leaks through later, strategy can still return `no_action`

### 3. Add explicit same-turn retry observability

Add structured retry telemetry whenever the loop restarts without committing a turn.

Minimum fields:

- `turn_label`
- `year`
- `turn`
- `reason`
- `reasons`
- `before_phase`
- `same_turn_retry: true`

Places worth instrumenting:

- pre-training invalid-state retry
- `action.func == "no_action"`
- `action.func == "skip_turn"`
- `run_action_with_review()` returning `"reassess"`
- any retry path after a failed action selection

This turns future incidents into a clear operator explanation instead of a timing puzzle.

### 4. Optional hardening: same-turn training scan cache

This is optional, not required for the first fix.

Add a lightweight cache for the most recent training scan keyed by a stable same-turn signature, for example:

- scenario
- year
- turn
- criteria
- current stats
- energy

Use it only when:

- the previous pass did not execute an action
- no item use/shop flow changed the turn state
- no explicit reassess reason exists

Do not use it across:

- post-item reassess
- post-shop refresh
- any real action execution

This is defense in depth. The primary fix should still be early validation.

## Implementation Plan

1. Extract shared validation result helper.
2. Add pre-training validation gate in `career_lobby()`.
3. Surface retry reason in operator snapshot and debug history.
4. Keep existing `Strategy.validate_state()` as a fallback guard.
5. Optionally add same-turn training scan cache only if duplicate scans still occur after the early gate.

## Acceptance Criteria

1. If `collect_main_state()` returns invalid `year`, `turn`, empty `criteria`, or unreadable stats, the loop retries before calling `collect_training_state()`.
2. In that retry case, the operator snapshot explicitly says why the retry happened.
3. Debug history shows a structured invalid-state retry event.
4. Normal same-turn action review in `check_only` still works.
5. Intentional re-scan flows after real state changes still work.

## Validation Plan

### Manual

1. Force or simulate a bad OCR read for one of:
   - `turn`
   - `year`
   - `criteria`
   - all stats
2. Confirm the bot retries without opening the training screen.
3. Confirm the operator snapshot shows the retry reason.
4. Confirm debug history contains the invalid-state retry event.
5. Confirm a normal valid turn still performs one training scan and reaches the final action preview.

### Regression

Verify these flows still behave correctly:

1. Trackblazer pre-debut `check_only`
2. Trackblazer normal training turn with inventory/shop scan
3. Reassess after Trackblazer item use
4. Race-day branch

## Adjacent Issue To Check While Implementing

[core/skeleton.py](/Users/loli/umaautomac/umamusume-auto-train/core/skeleton.py#L3041) reads `execution_intent` before waiting, then returns `"execute"` after the wait based on the stale pre-wait value at [core/skeleton.py](/Users/loli/umaautomac/umamusume-auto-train/core/skeleton.py#L3085).

That is separate from the duplicate training scan issue, but it lives in the same review-control area and should be sanity-checked during implementation so `check_only` behavior does not regress.
