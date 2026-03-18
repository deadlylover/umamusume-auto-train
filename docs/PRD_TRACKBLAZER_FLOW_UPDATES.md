# PRD: Trackblazer Flow Updates

## Status

Proposed. The repo now has:

- partial Trackblazer detection scaffolding
- an operator console with phase/sub-phase support
- a prose bot flow reference in `docs/BOT_FLOW.md`
- a gameplay/mechanics brief in `docs/MANT_TRACKBLAZER_BRIEF_REFERENCE.md`

What is still missing is a Trackblazer-specific flow model that the code can follow.

## Why This Exists

Trackblazer is not just another scenario with a few extra OCR fields.

It changes the structure of the career loop:

- scenario progression is driven by `Grade Points`
- racing is a core progression action, not an occasional exception
- `Shop Coins` and item economy matter to turn planning
- rival races can have separate value
- repeated races create fatigue risk
- the endgame is `Twinkle Star Climax`, a 3-race finale

The current main loop in `core/skeleton.py` is still shaped mostly like a standard training-first scenario with race branches layered on top. That is workable for URA and Unity, but it is not a strong planning surface for Trackblazer.

This PRD exists to move Trackblazer support from "add branches where needed" to "implement explicit scenario flow boundaries".

## Related Documents

- `docs/BOT_FLOW.md`
- `docs/MANT_TRACKBLAZER_BRIEF_REFERENCE.md`
- `docs/PRD_MANT_TRACKBLAZER.md`
- `docs/PRD_SEMI_AUTO_REVIEW_MODE.md`

## Product Goal

Define and implement a Trackblazer-aware runtime flow so the bot can reason about races, checkpoints, shop economy, fatigue, and finale planning as first-class parts of the turn loop.

## Non-Goals

- Perfect full Trackblazer automation in one pass
- Full item optimization from day one
- Replacing the generic runtime phases with a giant scenario-specific state machine
- Refactoring every scenario into a new architecture before Trackblazer becomes usable

## Problem Statement

Today the repo has two useful but incomplete things:

1. generic runtime phases in the operator console
2. Trackblazer-specific notes scattered across PRDs and placeholder OCR work

What it does not yet have is a canonical Trackblazer flow model that answers:

- what scenario state matters each turn
- when that state should be read
- how race-vs-training decisions should be gated
- where scenario-exclusive logic should live
- what the operator console should expose while the bot is deciding

Without that, Trackblazer work will keep accumulating as one-off branches in:

- `core/skeleton.py`
- `core/state.py`
- `core/trainings.py`
- `core/skill.py`

## Current Baseline

The current runtime loop already has useful top-level phases:

- `scanning_lobby`
- `collecting_main_state`
- `collecting_training_state`
- `pre_training`
- `evaluating_strategy`
- `pre_race`
- `waiting_for_confirmation`
- `executing_action`
- `recovering`

That top-level phase model is good enough to keep.

The missing layer is explicit Trackblazer sub-flow modeling inside those phases.

## Key Design Principle

Trackblazer should be modeled as a race-centric scenario with training windows.

That means:

- race planning is not just a fallback after training logic
- checkpoint progress must influence action selection directly
- shop/inventory state must be visible even before full shop automation exists
- fatigue and race cadence must be part of the planning model
- Twinkle Star Climax should be treated as its own endgame flow, not just another race day

## User Stories

- As a developer, I can inspect Trackblazer runs using a stable flow vocabulary instead of reading ad hoc branches in the loop.
- As a developer, I can see which Trackblazer sub-flow the bot is currently in.
- As a developer, I can add scenario-specific OCR or action logic without guessing where it belongs.
- As a user, I can run Trackblazer without the bot treating races as rare side actions.
- As a developer, I can debug why the bot preferred a race, training, shop visit, or skip.

## Functional Requirements

### 1. Canonical Trackblazer Flow Model

Trackblazer support must have a documented and code-aligned flow model.

At minimum, the scenario should explicitly recognize these decision areas:

- checkpoint progress
- Grade Points
- Race Bonus
- Shop Coins
- shop availability
- inventory summary
- rival race state
- race fatigue
- summer burst preparation
- Twinkle Star Climax planning

Requirement:

- these concepts must be reflected in documentation and runtime naming before adding more ad hoc scenario branches

### 2. Shared Phase/Sub-Phase Vocabulary

Trackblazer work should use the existing top-level runtime phases plus scenario-specific sub-phases.

Required Trackblazer-oriented sub-phases to support:

- `check_checkpoint_progress`
- `check_grade_points`
- `check_race_bonus`
- `check_shop_coins`
- `check_shop_state`
- `check_inventory`
- `check_rival_race_state`
- `check_race_fatigue`
- `prepare_summer_burst`
- `open_trackblazer_shop`
- `scan_trackblazer_shop`
- `preview_shop_purchase`
- `plan_twinkle_star_climax`

Requirement:

- sub-phase names should be reused consistently in runtime state, logs, and operator console snapshots

### 3. Minimum Trackblazer State Model

The bot should define a minimum documented scenario state model before broadening automation.

Candidate fields:

- `scenario_name`
- `checkpoint_window`
- `grade_points_current_window`
- `grade_points_target`
- `grade_points_remaining`
- `race_bonus`
- `shop_coins`
- `shop_available`
- `inventory_summary`
- `consecutive_race_count`
- `fatigue_risk`
- `rival_race_available`
- `tsc_phase`

Requirement:

- new Trackblazer OCR fields should map to one of these explicit concepts or another documented concept
- avoid growth of undocumented "mystery dict" state

### 4. Flow Integration In The Main Loop

The main loop should integrate Trackblazer at explicit boundaries.

Preferred boundary points:

- after scenario detection
- during main state collection
- before race-vs-training gating
- during race candidate evaluation
- before skill or shop entry
- before finale-specific execution

Requirement:

- avoid scattering Trackblazer checks across unrelated generic lobby handling branches

### 5. Race-Centric Decision Gate

Trackblazer should introduce an explicit decision boundary that answers:

- does this turn belong to a race-centric path
- does this turn belong to a training-centric path
- does this turn justify a shop or item interaction first

The decision should consider:

- checkpoint pressure
- Grade Point value
- Race Bonus pressure
- race fatigue
- rival value
- available training upside

Requirement:

- race selection should no longer appear in Trackblazer as only a late branch after generic training assumptions

### 6. Shop And Inventory Visibility

Even before full shop automation is complete, the flow must surface shop-related state as first-class debug information.

Minimum visibility:

- current Shop Coins
- whether shop is available
- basic inventory summary if detectable
- whether a shop visit is being considered

Requirement:

- operator/debug snapshot must expose the relevant values and OCR source regions

### 7. Twinkle Star Climax Flow

Trackblazer must treat `Twinkle Star Climax` as a distinct flow boundary.

At minimum, the implementation should:

- identify when normal checkpoint logic no longer applies
- expose a finale-specific sub-phase
- allow finale planning/execution to evolve separately from ordinary race choice

Requirement:

- do not bury TSC logic inside generic race day handling without clear sub-phase/state naming

### 8. Observability

Logs and the operator console must make Trackblazer decisions inspectable.

Required visibility:

- current top-level phase
- current Trackblazer sub-phase
- scenario-specific state summary
- whether the bot is in a race-centric or training-centric path
- proposed action
- relevant OCR/debug sources
- planned clicks for shop, race, and skill actions

## Proposed Implementation Shape

The implementation does not need a full rewrite, but it should move toward this structure:

1. `docs/BOT_FLOW.md` remains the prose reference.
2. Add a shared runtime phase/sub-phase constants module, for example `core/runtime_phases.py`.
3. Add a small Trackblazer flow/state helper module, for example:
   - `scenarios/trackblazer.py`
   - or `core/trackblazer_flow.py`
4. Keep generic loop ownership in `core/skeleton.py`.
5. Move Trackblazer-specific state interpretation and flow gating out of generic branches and into focused helper functions.

## Suggested Milestones

### Milestone 1: Naming And Runtime Model

- add shared phase/sub-phase constants
- align console/runtime snapshot naming
- document the minimum Trackblazer state model

### Milestone 2: Read-Only Trackblazer Flow State

- read and surface checkpoint progress inputs
- surface Race Bonus if detectable
- surface Shop Coins / shop availability
- surface fatigue/rival placeholders even if partially inferred

### Milestone 3: Trackblazer Decision Gate

- add explicit race-vs-training gating for Trackblazer
- expose the chosen path in runtime snapshot and logs
- keep final action selection compatible with existing `Action` flow

### Milestone 4: Shop/Finale Flow Hooks

- add dedicated shop-related sub-phases
- add dedicated Twinkle Star Climax sub-phases
- keep execution safe even if some logic remains preview-only

## Dependencies And Sequencing

This work is related to the semi-auto/operator-console PRD, but it is not fully blocked on "finishing everything" there.

Practical dependency:

- Trackblazer flow work should assume the current operator console/runtime snapshot system exists.

It is reasonable to start this PRD after the semi-auto work is stable enough to provide:

- runtime phase + sub-phase publishing
- OCR debug payloads
- planned click previews
- review/preview execution intents

It does not require every semi-auto polish item to be done first.

Recommended sequencing:

1. stabilize the current semi-auto/runtime snapshot foundation
2. implement Trackblazer flow naming and state model
3. add Trackblazer decision gates
4. add shop/finale-specific flow handling

## Acceptance Criteria

- There is a dedicated Trackblazer flow PRD and prose reference for the scenario’s runtime model.
- Runtime state uses explicit Trackblazer sub-phases instead of undocumented one-off branches.
- The operator console can show Trackblazer-specific flow state in a way that is useful for debugging.
- Trackblazer race-vs-training reasoning becomes a first-class part of the implementation.
- Shop/fatigue/checkpoint/finale concepts have named homes in the flow, even if some remain partially implemented.

## Risks

- Over-modeling the scenario before OCR support is ready
- Adding too many state fields without clear consumers
- Letting Trackblazer-specific logic leak back into generic lobby code
- Treating the endgame as generic race flow and losing debuggability

## Open Questions

- How much Race Bonus state can we reliably detect in v1?
- Is inventory state required for the first useful Trackblazer flow pass, or only shop coin visibility?
- Should shop logic initially be preview-only even if race/training actions are executable?
- Do we want a dedicated Trackblazer scorer module before full shop automation, or can we stage that after the new flow gates exist?
