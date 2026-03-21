# PRD: MANT / Trackblazer Scenario Support

## Status

In progress. As of March 21, 2026, the branch now has working Trackblazer detection, operator-console visibility, inventory scan/use plumbing, shop scan/purchase plumbing, policy-driven buy/use planning, and a first-pass Trackblazer race-vs-training gate. The main critical implementation still missing is richer race candidate scoring driven by real scenario state, plus the scenario-state/scoring work that should feed it.

## Current Progress

Implemented in support of Trackblazer bring-up:

- [x] Operator/debug console now exists and can be used while tuning a new scenario.
- [x] OCR region adjuster can be launched directly from that console.
- [x] Scenario banner asset was added at `assets/scenario_banner/trackblazer.png`.
- [x] `core/skeleton.py` now treats banner filenames as scenario keys and uses `trackblazer` as the canonical internal name, with `mant` retained only as a legacy alias in some branches/comments.
- [x] Operator console/runtime state now supports execution intents:
  - `check_only`
  - `execute`
- [x] Operator console now includes:
  - sub-phase display
  - OCR debug pane
  - copy-to-clipboard actions for summary/trainings/OCR debug
- [x] Separate placeholder MANT/Trackblazer region entries were added so the scenario can be tuned independently from default/Unity:
  - energy
  - turn
  - year
  - criteria
  - failure
  - stat gains
  - support card icon region
  - initial grade point / shop coin / shop button placeholders
- [x] `core/state.py` now recognizes `mant` / `trackblazer` as a separate scenario branch for those placeholder regions.
- [x] Scenario detection is now wired through the main loop and logs the detected scenario before main state collection begins.
- [x] Trackblazer placeholder OCR provenance now appears in the console snapshot for:
  - grade points
  - shop coins
  - shop button region
- [x] Planned-click preview payloads now exist for race actions and skill-buy flow, so the console can show intended clicks without committing them in non-execute intents.
- [x] Skill purchase review is now a distinct inspectable console path with sub-phases such as:
  - `evaluate_skill_purchase`
  - `scan_skill_list`
  - `preview_skill_purchase`
  - `confirm_skill_purchase`
- [x] Trackblazer inventory open/scan/close and the non-destructive item-use test flow are implemented in `scenarios/trackblazer.py`.
- [x] Inventory scan timing, held-quantity OCR, and per-item debug provenance are surfaced in the operator console snapshot.
- [x] Shop entry detection and refresh-popup dismissal are wired into the lobby loop.
- [x] Trackblazer shop scan now uses scrollbar-aware reset/seek plus buffered frame capture during a continuous scrollbar drag.
- [x] Shop scan timing now separates drag runtime, buffered capture, overlapped analysis, and total wall time in the flow snapshot.
- [x] Shop row selection scaffolding now exists for "find item while scrolling, seek back to its band, and click only that row checkbox without pressing confirm".
- [x] Canonical Trackblazer shop policy now lives in `core/trackblazer_shop.py`, with timeline-aware priority preview and quantity caps surfaced in the console.
- [x] Canonical Trackblazer item-use policy now lives in `core/trackblazer_item_use.py`, with selected-action-aware `Would Use` / `Deferred Use` planning.
- [x] `core/skeleton.py` now attaches a Trackblazer pre-action item-use plan and Trackblazer shop buy plan to the chosen action before review/execute.
- [x] `run_action_with_review()` now runs Trackblazer pre-action steps in the real execution path:
  - policy-driven shop purchases before the main action
  - policy-driven training-item use before the main action
  - reassessment after `Reset Whistle`
- [x] `scenarios/trackblazer.py::execute_trackblazer_shop_purchases(...)` now implements the production purchase path:
  - enter shop
  - select planned rows
  - press confirm
  - dismiss after-sale prompt
  - close the shop
- [x] `scenarios/trackblazer.py::execute_training_items(...)` now implements the production item-use path with dry-run / confirm-only / full commit modes, increment-target verification, confirm-use detection, follow-up confirm handling, and close/verify behavior.
- [x] In `check_only`, the main loop now does automatic non-destructive Trackblazer inventory scan plus one shop scan/refresh pass per turn so review snapshots include current held items, visible shop items, and would-buy / would-use output.
- [x] The operator console now exposes dedicated manual checks for Trackblazer inventory scan, Trackblazer item-selection test, and Trackblazer shop scan, and renders their flow timings in the Timing pane.
- [x] A first-pass Trackblazer race gate now lives in `core/trackblazer_race_logic.py` and is wired into `core/skeleton.py` under the `evaluate_trackblazer_race` sub-phase.
- [x] The race gate currently implements:
  - force `G1`
  - summer bias toward training
  - weak-training threshold routing toward optional races
  - rival-indicator bias toward racing
- [x] Trackblazer race decision payloads now appear in the review snapshot and operator console so live testing feedback can be tied back to concrete gate inputs.

Shop-related template assets added:

- `assets/icons/shop_refresh.png` (264x235px) — detects the shop refresh popup during lobby scanning. Wired into `career_lobby()` in `core/skeleton.py`: when detected, the bot logs the refresh and dismisses the popup with cancel.
- `assets/buttons/shop_refresh_shop_button.png` (118x50px) — the "Shop" button on the shop refresh popup. Not yet wired; needed when implementing "enter shop from popup" logic.
- `assets/buttons/shop_enter_lobby.png` (123x81px) — the shop button on the main lobby screen. Not yet wired; needed for entering the shop from the lobby when the bot decides to spend coins.

Trackblazer shop item assets added under `assets/trackblazer/`:

**Shop items (item icon templates for inventory/shop recognition):**

- `grilled_carrots.png` — Grilled Carrots. Energy recovery item (restores energy/vitality).
- `guts_notepad.png` — Guts Notepad. Training item that boosts Guts training gains.
- `miracle_cure.png` — Miracle Cure. Condition recovery item (cures status ailments / bad conditions).
- `motivating_megaphone.png` — Motivating Megaphone. Motivation/mood booster item.
- `stamina_ankle_weights.png` — Stamina Ankle Weights. Training item that boosts Stamina training gains.
- `wit_manual.png` — Wit Manual. Training item that boosts Wit/Intelligence training gains.
- `yumy_cat_food.png` — Yumy Cat Food. Energy recovery item (restores energy/vitality).

**Shop purchase flow UI templates:**

- `shop_confirm.png` — Green "Confirm" button on the shop purchase dialog. Used to confirm buying an item.
- `shop_aftersale_close.png` — "Close" button on the post-purchase dialog. Dismisses the after-sale screen without using the item.
- `shop_aftersale_confirm_use_available.png` — Green "Confirm Use" button (active/available state). Appears post-purchase when the item can be used immediately.
- `shop_aftersale_confirm_use_unavailable.png` — Grey/dimmed "Confirm Use" button (unavailable state). Appears post-purchase when the item cannot be used right now (e.g. not applicable to current state).
- `shop_aftersale_confirm_use_increment_item.png` — Green "+" increment arrow on the post-purchase use dialog. Used to increase quantity of the item to use.

**Item use flow UI templates:**

- `shop_use_training_items.png` — Green "Use Training Items" button. Enters the training-item use screen from inventory.
- `shop_use_back.png` — "Back" button on the item-use screen. Returns to the previous screen.
- `training_items.png` — "Training Items" tab/label text. Identifies the training items section in the inventory/use UI.

**Item selection UI templates:**

- `select_checked.png` — Green checkmark (selected state). Indicates an item row is currently selected for use.
- `select_unchecked.png` — Grey/empty checkbox (unselected state). Indicates an item row is not selected.

Highest-priority remaining work:

- Richer Trackblazer race decision logic:
  - candidate race evaluation using checkpoint pressure, rival value, fatigue/race cadence, and finale context
  - live race-list-backed grade detection instead of schedule-only assumptions
  - better integration than the current generic race-day / mission / goal / scheduled-race branches plus the first-pass race gate
- Real Trackblazer scenario-state extraction for the race planner:
  - Grade Points / checkpoint progress
  - race bonus / progression pressure
  - fatigue or consecutive-race risk
  - Twinkle Star Climax phase awareness
- Trackblazer training/scenario scoring in `core/trainings.py` so training value and race value are compared on scenario-aware inputs rather than generic logic alone
- Live-run hardening:
  - execute a few real turns
  - validate open/close reliability for inventory/shop in normal play
  - tune OCR thresholds and policy defaults from actual run logs

Still not implemented:

- real Grade Point/checkpoint OCR that feeds decision-making
- Trackblazer-specific training score contribution in `core/trainings.py`
- first-class Trackblazer race candidate scoring and selection
- TSC-specific planning/execution flow
- item price OCR from the shop UI itself
- fatigue-aware race cadence logic
- checkpoint / Grade Point / Race Bonus aware race forcing

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

The canonical flow vocabulary for this work should follow [`docs/BOT_FLOW.md`](./BOT_FLOW.md), so Trackblazer additions land as explicit sub-flows within the existing runtime phases instead of scattered one-off branches in the main loop.

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
- Note for later recovery work: startup checkpoint detection should prefer a confirmed/stable career screen, but the bot should eventually also support starting from arbitrary in-run screens and wandering back to the main training screen on its own, such as race progress/result flows or other interrupted states.

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
- current execution intent: `check_only` or `execute`
- the current Trackblazer sub-phase, especially for shop, inventory, skill, and race handling
- the OCR region/constants used for the current Trackblazer read
- the intended click target(s) before any skill/shop/race commit happens

The console should be considered part of the acceptance path for Trackblazer debugging, not a stretch goal.

## Debug Console Dependency

Trackblazer implementation should assume a shared operator/debug console exists with:

- a live state panel
- a proposed-action panel
- a ranked training panel
- a phase/state visualization
- a visible blocked/error state when recognition fails
- an OCR/debug panel that shows active region keys, adjusted bounds, parsed values, and crop paths
- copy-to-clipboard controls so console output can be pasted back into debugging threads

This prevents the scenario bring-up loop from relying on terminal log scraping.

### Required Trackblazer Sub-Flows In The Console

The console flow should be expanded beyond generic "collect state" and "execute action" labels. Based on the Trackblazer brief, the operator needs to see explicit scenario sub-flows for:

- `check_grade_points`
  read current Grade Points, checkpoint target, and whether current points carry meaningful progression value for this window
- `check_shop_coins`
  read current Shop Coins and expose the OCR/debug source for that value
- `check_shop_state`
  determine whether the Special Shop is available on this screen/turn and whether a visit is recommended
- `check_inventory`
  inspect currently held items, especially training items, energy items, whistles, megaphones, and reserved finals resources
- `check_rival_race_state`
  detect whether a candidate race has a rival marker / hint value
- `open_skill_menu`
  enter the skill screen when appropriate
- `scan_skill_list`
  read visible skill entries and match them against configured targets
- `preview_skill_purchase`
  show the skill row(s) and `Learn` button click(s) the bot intends to use
- `open_trackblazer_shop`
  enter the shop when appropriate
- `scan_trackblazer_shop`
  read visible item entries, prices, and any inventory-related context
- `preview_shop_purchase`
  show which item slot and confirmation button the bot intends to click
- `open_race_menu`
  enter the race list
- `scan_race_candidates`
  read race cards and expose inputs relevant to Trackblazer scoring:
  grade point value, coin value, rival marker, distance/terrain implications, and fatigue context
- `preview_race_selection`
  show the race entry the bot intends to click before committing

For OCR tuning, each of these should be able to run in a non-committing path where the bot reads and evaluates but does not finalize the click sequence.

## Trackblazer Shop & Item Flows

The shop and item-use flows have been mapped from in-game screenshots. Template assets cover the full purchase-and-use cycle.

### Shop Purchase Flow

1. **Enter shop** — via lobby button (`shop_enter_lobby.png`) or shop refresh popup (`shop_refresh_shop_button.png`).
2. **Browse items** — item icons (grilled_carrots, guts_notepad, miracle_cure, motivating_megaphone, stamina_ankle_weights, wit_manual, yumy_cat_food) are used to identify what's available and match against a buy priority list.
3. **Confirm purchase** — tap `shop_confirm.png` on the purchase dialog.
4. **After-sale dialog** — the game shows a post-purchase screen with options:
   - **Use now** — `shop_aftersale_confirm_use_available.png` (green, active) if the item is usable in the current context. Tap the `+` increment (`shop_aftersale_confirm_use_increment_item.png`) to adjust quantity, then confirm.
   - **Cannot use now** — `shop_aftersale_confirm_use_unavailable.png` (grey, dimmed) when the item doesn't apply right now.
   - **Close** — `shop_aftersale_close.png` to dismiss and bank the item for later.

### Item Use Flow (from inventory)

1. **Enter item use** — tap `shop_use_training_items.png` ("Use Training Items" button).
2. **Select training items tab** — match `training_items.png` label to confirm correct tab.
3. **Select/deselect items** — use `select_checked.png` / `select_unchecked.png` to detect and toggle item rows.
4. **Go back** — `shop_use_back.png` to return to previous screen.

### Known Item Categories

| Item | Category | Effect |
|------|----------|--------|
| Grilled Carrots | Energy | Restores energy/vitality |
| Yumy Cat Food | Energy | Restores energy/vitality |
| Miracle Cure | Condition | Cures bad conditions/ailments |
| Motivating Megaphone | Mood | Boosts motivation/mood |
| Guts Notepad | Training boost | Boosts Guts training gains |
| Stamina Ankle Weights | Training boost | Boosts Stamina training gains |
| Wit Manual | Training boost | Boosts Wit/Intelligence training gains |

### Not Yet Captured

- Speed / Power training boost item icons (if they exist in the shop rotation)
- Whistle / finals-specific item icons
- Item price regions for OCR
- Item quantity/stock indicators
- Shop coin balance on the shop screen itself

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

- [x] Add scenario banner asset(s).
- [x] Extend scenario detection to return the new scenario name.
- [ ] Verify default runs and Unity runs still detect correctly.
- [x] Ensure unknown Trackblazer screens do not cause infinite `non_match_count` looping without logs.
  - Shop refresh popup is now detected and auto-dismissed with cancel in `career_lobby()`.

Acceptance:

- The bot logs the detected scenario correctly on a Trackblazer run.
- No regression in default/Unity startup.

### Phase 2: Read-Only Scenario State

- [x] Add Trackblazer OCR/template regions to `utils/constants.py`.
- [x] Implement initial scenario branching in `core/state.py`.
- [ ] Replace placeholder MANT regions with tuned scenario-specific values.
- [ ] Implement real Trackblazer state extraction for grade points / checkpoint progress / race pressure.
- [x] Implement real Trackblazer shop coin extraction on the shop screen.
- [x] Surface placeholder Trackblazer OCR provenance in the operator/debug console snapshot.
- [x] Implement inventory/item-state extraction needed for Trackblazer debugging.
- [ ] Save debug crops for each new recognition area.
- [ ] Document every new state key and its expected range/meaning.
- [x] Feed inventory/shop state fields into the operator/debug console snapshot.

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

- [x] Create `scenarios/trackblazer.py`.
- [x] Add routing from `core/skeleton.py` for inventory scan, shop scan, pre-action item use, and pre-action shop purchase plumbing.
- [ ] Handle mandatory confirmation / route-selection / checkpoint screens.
- [ ] Add retry/escape behavior when the handler cannot confirm state.
- [x] Add `check_only` review paths for skill buying and generic action/race selection review.
- [x] Add `check_only` review paths for Trackblazer shop interaction and inventory checks.
- [ ] Add first-class Trackblazer race-routing and race review paths beyond the current generic race preview flow.

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

- [x] Decide canonical scenario key: `trackblazer` with `mant` accepted as a legacy alias where needed.
- [x] Add `assets/scenario_banner/<name>.png`.
- [x] Add shop item icon templates under `assets/trackblazer/` (grilled_carrots, guts_notepad, miracle_cure, motivating_megaphone, stamina_ankle_weights, wit_manual, yumy_cat_food).
- [x] Add shop purchase flow UI templates under `assets/trackblazer/` (shop_confirm, shop_aftersale_close, shop_aftersale_confirm_use_available, shop_aftersale_confirm_use_unavailable, shop_aftersale_confirm_use_increment_item).
- [x] Add item use flow UI templates under `assets/trackblazer/` (shop_use_training_items, shop_use_back, training_items).
- [x] Add item selection UI templates under `assets/trackblazer/` (select_checked, select_unchecked).
- [x] Add shop refresh popup detection and dismissal in `core/skeleton.py` lobby loop.
  - `assets/icons/shop_refresh.png` — detect popup
  - `assets/buttons/shop_refresh_shop_button.png` — shop entry from popup (not yet wired)
  - `assets/buttons/shop_enter_lobby.png` — shop entry from lobby (not yet wired)
- [x] Update `utils/constants.py` with Trackblazer-specific regions and template maps.
- [x] Update `core/state.py` to read Trackblazer inventory state and surface debug/timing data.
- [x] Update `core/skeleton.py` detection/routing for scenario-aware inventory handling and console snapshots.
- [ ] Update `core/trainings.py` with scenario score function and debug fields.
- [x] Add `scenarios/trackblazer.py` for inventory scanning, item-use testing, shop scanning, shop purchase execution, rival-race scouting, and shop-entry helpers.
- [x] Ensure Trackblazer fields are included in the operator/debug console snapshot.
- [x] Add Trackblazer console/manual coverage for inventory and shop checks.
- [ ] Add Trackblazer console sub-phases for race selection and race planning.
- [x] Add skill-buy console sub-phases and review flow.
- [x] Add Trackblazer OCR provenance entries showing region key and adjusted bounds.
- [ ] Add Trackblazer planned-click preview entries for the concrete shop-purchase click sequence.
- [x] Add planned-click preview entries for race and skill actions.
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

- What exact mix of checkpoint pressure, rival value, fatigue, and TSC prep should dominate race selection?
- Does the scenario need its own race-day or event handling logic beyond the current generic branches?
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
