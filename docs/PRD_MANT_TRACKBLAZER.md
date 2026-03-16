# PRD: MANT / Trackblazer Scenario Support

## Status

Draft TODO PRD for implementing support for the new MANT / Trackblazer scenario in the new architecture.

## Current Progress

Implemented in support of Trackblazer bring-up:

- Operator/debug console now exists and can be used while tuning a new scenario.
- OCR region adjuster can be launched directly from that console.
- Separate placeholder MANT/Trackblazer region entries were added so the scenario can be tuned independently from default/Unity:
  - energy
  - turn
  - year
  - criteria
  - failure
  - stat gains
  - support card icon region
  - initial grade point / shop coin / shop button placeholders
- `core/state.py` now recognizes `mant` / `trackblazer` as a separate scenario branch for those placeholder regions.

Not implemented yet:

- scenario banner asset/detection
- real Trackblazer OCR extraction for grade points / shop coins / shop state
- Trackblazer scoring
- Trackblazer shop/item logic
- Trackblazer-specific action routing

## Why This Exists

The current bot supports the standard flow plus Unity Cup-specific behavior. The new scenario needs first-class support in the same places Unity already touches:

- scenario detection in `core/skeleton.py`
- scenario-aware OCR regions in `utils/constants.py` and `core/state.py`
- scenario-specific training scoring in `core/trainings.py`
- scenario-specific action handling in `scenarios/`
- config and debug visibility in the web UI / logs

The goal is not just "recognize the banner". The bot must be able to:

1. Detect that the run is MANT / Trackblazer.
2. Read enough scenario state to make training decisions reliably.
3. Score training options with scenario-specific value.
4. Handle any mandatory scenario-exclusive actions/screens without getting stuck.
5. Degrade safely when recognition is incomplete.

Trackblazer support should be built together with the new operator/debug console, not after it. The scenario is new enough that "implement first, inspect logs later" will be too slow.

## Non-Goals

- Perfect full automation on day one.
- Supporting every niche event/branch before the main training loop is stable.
- Treating the operator/debug console as optional for Trackblazer rollout.

Semi-auto review mode and the operator/debug console are covered in a separate PRD because they should work across all scenarios, but Trackblazer should assume that tooling exists.

## Current Baseline

The repo already has a working pattern for adding scenarios:

- `core/skeleton.py` detects scenario and routes Unity Cup interruptions.
- `utils/constants.py` keeps separate Unity OCR regions.
- `core/state.py` switches OCR logic based on `constants.SCENARIO_NAME`.
- `core/trainings.py` adds scenario-specific score via `add_scenario_gimmick_score()`.
- `scenarios/unity.py` isolates scenario-exclusive flow.

Trackblazer should follow the same shape instead of introducing one-off branches throughout the codebase.

## Product Goal

Implement MANT / Trackblazer support with a staged rollout:

1. Safe detection and basic compatibility.
2. Read-only state extraction for scenario mechanics.
3. Decision visibility in the operator/debug console.
4. Training scoring that values the new mechanic.
5. Action handling for scenario-exclusive screens.
6. Configurable tuning and debug output.

## Success Criteria

- The bot detects MANT / Trackblazer runs reliably from the career screen.
- `collect_main_state()` and `collect_training_state()` do not regress on existing scenarios.
- The operator/debug console can show Trackblazer-specific state without requiring terminal log reading.
- Training decisions include Trackblazer-specific value instead of treating the scenario as standard URA.
- Scenario-exclusive prompts/screens are handled or explicitly surfaced without infinite looping.
- Logs contain enough detail to compare "raw OCR" vs "chosen action" when runs go wrong.

## User Stories

- As a user, I can start a MANT / Trackblazer run and the bot recognizes the scenario automatically.
- As a user, I can see from logs what the bot believed the scenario-specific state was.
- As a developer, I can see Trackblazer state, proposed action, and current bot phase in a separate GUI window.
- As a user, I can tune scenario weights without editing code.
- As a developer, I can add or adjust Trackblazer OCR regions without breaking Unity or default flows.

## Functional Requirements

### 1. Scenario Detection

- Add a new scenario banner asset under `assets/scenario_banner/`.
- Ensure `detect_scenario()` returns a stable identifier such as `mant` or `trackblazer`.
- Decide on one canonical internal name and use it everywhere.
- If detection fails, continue to fall back to `default`, but log enough context to diagnose.

### 2. Scenario State Model

Define the minimum Trackblazer-specific state required for decision-making. This should be explicit before implementation.

Candidate fields:

- current scenario phase / chapter
- scenario resource meter(s)
- training bonus indicators
- rival / route / node / expedition state
- limited-use action availability
- turn-locked or checkpoint actions

Requirement:

- Add new keys to the training result/state object only after defining their source and consumer.
- Avoid "mystery dict" growth with undocumented fields.

### 3. OCR / Template Support

- Add dedicated OCR regions in `utils/constants.py` if the scenario layout differs from default/Unity.
- Extend `core/state.py` to read scenario-specific fields.
- Prefer the existing `if constants.SCENARIO_NAME == ...` structure initially.
- If Trackblazer needs many more branches, refactor to a small scenario registry instead of stacking more `if` statements.

### 4. Training Evaluation

- Extend `create_training_score_entry()` to expose Trackblazer metrics needed for debugging.
- Extend `add_scenario_gimmick_score()` to handle Trackblazer.
- Add a `trackblazer_training_score()` function mirroring `unity_training_score()`.
- Keep the base scoring logic reusable; the scenario score should be additive and tunable.

### 5. Scenario-Exclusive Actions

If the scenario introduces screens that interrupt the normal training/rest/race/event loop:

- implement a dedicated handler under `scenarios/`, likely `scenarios/trackblazer.py`
- keep routing logic in `core/skeleton.py` minimal
- make the handler idempotent so repeated detection does not cause duplicate clicks

### 6. Configuration and Tuning

Add config surface only for values that will actually need tuning:

- scenario-specific weight(s)
- optional enable/disable flag if rollout requires gating
- optional debug verbosity for scenario OCR

If additional config is added:

- `config.template.json` must include defaults
- `core/config.py` must load it
- web config types/forms must be updated if the field should be user-editable

### 7. Observability

Logs must show:

- detected scenario
- scenario-specific OCR values
- per-training Trackblazer score contribution
- selected action and rejected alternatives when relevant

Debug artifacts should make it easy to capture:

- scenario banner crop
- scenario-specific OCR crops
- per-training data snapshot

Operator console must show:

- current bot phase/state
- current scenario name
- current OCR/state summary
- proposed next action
- Trackblazer-specific fields used in scoring
- current error / recovery state if blocked

The console should be considered part of the acceptance path for Trackblazer debugging, not a stretch goal.

## Debug Console Dependency

Trackblazer implementation should assume a shared operator/debug console exists with:

- a live state panel
- a proposed-action panel
- a ranked training panel
- a phase/state visualization
- a visible blocked/error state when recognition fails

This prevents the scenario bring-up loop from relying on terminal log scraping.

## Architecture Requirements

### Preferred File Boundaries

- `core/skeleton.py`: detection and routing only
- `core/state.py`: OCR/state extraction
- `core/trainings.py`: scoring
- `scenarios/trackblazer.py`: exclusive scenario action flow
- `utils/constants.py`: regions and template maps

### Avoid

- embedding scenario-click logic deep inside `Action.run()`
- scattering Trackblazer branches across unrelated action helpers
- hard-coding temporary weights without config or comments

## Implementation Plan

### Phase 0: Discovery

- [ ] Confirm the exact in-game English naming to use in docs/config/logs.
- [ ] Capture representative screenshots for all major Trackblazer screens at the target layout.
- [ ] Identify whether the scenario uses a different top bar, training panel, or bottom action layout.
- [ ] List scenario-exclusive screens that can appear during the normal turn loop.
- [ ] Define the minimum scenario metrics required for a "good enough" first scorer.

### Phase 1: Detection and Safe Compatibility

- [ ] Add scenario banner asset(s).
- [ ] Extend scenario detection to return the new scenario name.
- [ ] Verify default runs and Unity runs still detect correctly.
- [ ] Ensure unknown Trackblazer screens do not cause infinite `non_match_count` looping without logs.

Acceptance:

- The bot logs the detected scenario correctly on a Trackblazer run.
- No regression in default/Unity startup.

### Phase 2: Read-Only Scenario State

- [x] Add Trackblazer OCR/template regions to `utils/constants.py`.
- [x] Implement initial scenario branching in `core/state.py`.
- [ ] Replace placeholder MANT regions with tuned scenario-specific values.
- [ ] Implement real Trackblazer state extraction for grade points / shop coins / shop state.
- [ ] Save debug crops for each new recognition area.
- [ ] Document every new state key and its expected range/meaning.
- [ ] Feed those new state fields into the operator/debug console snapshot.

Acceptance:

- Logs and the operator/debug console can show stable scenario values across repeated scans of the same screen.

### Phase 3: Training Scoring

- [ ] Add Trackblazer fields to `create_training_score_entry()`.
- [ ] Implement `trackblazer_training_score()`.
- [ ] Extend `add_scenario_gimmick_score()` dispatch.
- [ ] Add config default(s) for Trackblazer weighting if `scenario_gimmick_weight` alone is too coarse.
- [ ] Validate that low-confidence OCR fails soft instead of overpowering base scoring.

Acceptance:

- Training selection changes in plausible ways when Trackblazer-specific value changes.
- Logs show both base score and scenario-added score.

### Phase 4: Exclusive Action Handling

- [ ] Create `scenarios/trackblazer.py`.
- [ ] Add routing from `core/skeleton.py`.
- [ ] Handle mandatory confirmation / route-selection / checkpoint screens.
- [ ] Add retry/escape behavior when the handler cannot confirm state.

Acceptance:

- The bot can traverse the scenario’s mandatory side screens without manual recovery in common cases.

### Phase 5: Rollout and Hardening

- [ ] Add dry-run verification steps.
- [ ] Add a known-issues section to the PR or docs.
- [ ] Capture at least one full run log for review.
- [ ] Capture console screenshots for at least one bad-state and one healthy-state turn.
- [ ] Tune thresholds/weights after real-run observation.

Acceptance:

- A Trackblazer run can progress through multiple turns without misclick loops.

## Technical TODOs

- [ ] Decide canonical scenario key: `mant`, `trackblazer`, or `mant_trackblazer`.
- [ ] Add `assets/scenario_banner/<name>.png`.
- [ ] Add any new scenario assets under a dedicated `assets/trackblazer/` directory.
- [ ] Update `core/skeleton.py` detection/routing.
- [ ] Update `utils/constants.py` with Trackblazer-specific regions if needed.
- [ ] Update `core/state.py` to read Trackblazer state.
- [ ] Update `core/trainings.py` with scenario score function and debug fields.
- [ ] Add `scenarios/trackblazer.py` if scenario actions exist.
- [ ] Ensure Trackblazer fields are included in the operator/debug console snapshot.
- [ ] Update `config.template.json` and `core/config.py` if new knobs are needed.
- [ ] Update web UI types/components if new knobs should be user-editable.
- [ ] Add logs/debug capture guidance to README or follow-up docs.

## Risks

- OCR drift if Trackblazer uses a different HUD alignment than URA/Unity.
- Overfitting the scoring model to one deck/run archetype.
- Scenario handler complexity creeping into the main loop.
- False positives from banner detection if assets are too loose.
- Shipping a scorer before the scenario state is actually trustworthy.

## Open Questions

- What exact scenario mechanic matters most for decision quality in Trackblazer?
- Does the scenario need its own race-day or event handling logic?
- Are the training panel and support icons placed differently enough to require dedicated regions?
- Is there an upstream implementation to port later, or are we designing this from scratch?
- Should Trackblazer tuning reuse `scenario_gimmick_weight`, or does it need separate sub-weights?

## Recommended Delivery Strategy

Deliver this as small PRs instead of one large merge:

1. Runtime operator/debug console skeleton
2. Detection + assets + Trackblazer state snapshot
3. Read-only OCR/state extraction
4. Scoring integration
5. Scenario-exclusive action flow
6. Tuning/UI polish

That keeps regressions easier to isolate and makes debugging much less painful.
