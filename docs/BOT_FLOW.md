# Bot Flow Reference

This document is the human-readable reference for the bot's runtime flow.

It exists so scenario work, especially MANT / Trackblazer, can be planned against a shared set of phases instead of being implemented as one-off branches inside the main loop.

## Why This Matters

Today the bot flow is mostly encoded in:

- `core/skeleton.py`
- `core/operator_console.py`
- scenario handlers such as `scenarios/unity.py`

That is enough for execution, but not ideal as a planning surface. A documented flow makes it easier to:

- add scenario-exclusive steps without losing the main loop shape
- decide where OCR state belongs
- decide where action routing belongs
- give AI agents a stable reference when inspecting the repo

## Current Source Of Truth

The current executable flow lives in `core/skeleton.py`.

The current top-level operator phases displayed in the console live in `core/operator_console.py`:

- `idle`
- `focusing_window`
- `scanning_lobby`
- `collecting_main_state`
- `collecting_training_state`
- `pre_training`
- `evaluating_strategy`
- `pre_race`
- `waiting_for_confirmation`
- `executing_action`
- `recovering`

Those are runtime/UI phases, not a full domain model of every in-game state.

## Recommended Modeling Rule

Use two levels:

1. Top-level runtime phases
2. More specific sub-phases for screen- or scenario-level behavior

This matches the current operator console shape and keeps the main loop readable.

Top-level phases should stay small and stable.
Sub-phases should carry most of the scenario detail.

## Canonical Main Loop

The current bot loop can be described like this:

1. Focus or recover the game window if needed.
2. Scan the current screen until a stable career/lobby screen is confirmed.
3. Resolve interruptions that are not normal training decisions:
   - event choice
   - next / continue screens
   - retry / cancel flows
   - claw machine
   - scenario interruption such as Unity Cup
4. Detect scenario when enough UI is visible.
5. Collect main state from the lobby screen.
6. If the turn is already a forced race screen, go into race flow.
7. Check high-priority race opportunities:
   - mission race
   - scheduled race
   - goal race
8. If not racing immediately, collect training state.
9. Optionally review skill purchase.
10. Evaluate strategy and choose an action.
11. Preview / confirm action if review mode is active.
12. Execute the action.
13. Return to lobby scanning and repeat.

## Suggested Domain Flow Vocabulary

For planning and scenario design, these labels are more useful than only the UI phases:

- `lobby_scan`
  The bot is trying to recognize what screen it is on and get back to the stable career screen.
- `interruption_resolution`
  The bot is handling event dialogs, result screens, retry/cancel screens, scenario popups, and other non-decision screens.
- `scenario_detection`
  The bot identifies the active scenario from stable UI.
- `main_state_scan`
  The bot reads turn, year, criteria, energy, mood, aptitudes, and other always-needed state.
- `race_gate_check`
  The bot determines whether the turn is or should become a race flow.
- `training_state_scan`
  The bot opens the training menu and reads candidate training data.
- `skill_review`
  The bot evaluates whether to enter the skill screen and buy skills.
- `action_selection`
  The strategy layer ranks actions and picks one.
- `action_preview`
  The operator console exposes OCR evidence and intended clicks. In `check_only` mode the bot pauses here.
- `action_execution`
  The selected action is committed. Pressing Continue (F2) during `action_preview` in `check_only` mode triggers a one-shot execute — the full sequence (shop purchases → inventory refresh → item use → reassess → action) runs once, then the bot returns to `check_only` for the next turn. This supports a step-through walkthrough workflow.
- `post_action_resolution`
  The bot handles screens that appear after training, races, events, or other
  committed actions but before the stable lobby is back. This is where
  scenario popups like Trackblazer shop sale / refresh and scheduled race
  notices should live.
- `recovery`
  The bot retries, skips, or returns to lobby after a failed or invalid step.

These domain labels do not need to replace the current runtime phases, but scenario docs should use them consistently.

## Suggested In-Game Sub-Phases

These are the kinds of sub-phases that are useful to document and expose.

### Lobby / Generic

- `scan_lobby_init`
- `scan_lobby_templates`
- `scan_lobby_waiting_for_tazuna`
- `detect_scenario_open_details`
- `detect_scenario_match_banner`
- `detect_scenario_confirmed`

### Training Decision Path

- `evaluate_training_action`
- `scan_training_options`
- `rank_training_options`
- `preview_action_clicks`
- `confirm_training`

### Race Decision Path

- `evaluate_race_action`
- `open_race_menu`
- `scan_race_candidates`
- `preview_race_selection`
- `confirm_race_entry`
- `race_day_flow`

### Skill Path

- `evaluate_skill_purchase`
- `open_skill_menu`
- `scan_skill_list`
- `preview_skill_purchase`
- `confirm_skill_purchase`

### Recovery Path

- `post_action_resolution`
- `resolve_post_action_popup`
- `state_invalid_retry`
- `action_failed_retry`
- `return_to_lobby`

## Scenario Extension Rule

Scenario code should hook into explicit flow boundaries, not arbitrary points in the loop.

Preferred extension points:

- after `scenario_detection`
- during `main_state_scan` for scenario-specific OCR
- during `training_state_scan` for scenario-specific training metrics
- during `action_selection` for scenario-specific scoring
- inside dedicated scenario handlers for exclusive screens/actions

Avoid:

- adding unrelated scenario branches throughout the generic lobby scan
- treating scenario post-action popups as generic `cancel` / `next` cleanup
- adding undocumented keys to the state dict
- mixing scenario-exclusive shop/inventory logic directly into generic training choice code

## Trackblazer Mapping

For MANT / Trackblazer, the likely scenario-specific sub-phases are:

- `check_checkpoint_progress`
- `check_grade_points`
- `check_race_bonus`
- `check_shop_coins`
- `check_shop_state`
- `check_inventory`
- `check_rival_race_state`
- `check_race_fatigue`
- `open_trackblazer_shop`
- `scan_trackblazer_shop`
- `preview_shop_purchase`
- `post_action_resolution`
- `resolve_shop_refresh_popup`
- `resolve_scheduled_race_popup`
- `prepare_summer_burst`
- `plan_twinkle_star_climax`

Those should be treated as Trackblazer sub-flows under the existing top-level phases, not as a separate second main loop.

For the canonical training-item behavior inside those sub-flows, including `Reset Whistle` reassess handling, see [`docs/TRACKBLAZER_PRE_ACTION_ITEM_FLOW.md`](./TRACKBLAZER_PRE_ACTION_ITEM_FLOW.md).

Example mapping:

- `collecting_main_state`
  with Trackblazer sub-phases such as `check_checkpoint_progress`, `check_grade_points`, `check_race_bonus`, and `check_shop_state`
- `collecting_training_state`
  with Trackblazer-specific training bonus reads
- `pre_training` or `pre_race`
  with Trackblazer-specific shop, fatigue, and rival checks
- `executing_action`
  with Trackblazer shop purchase or race selection actions

## Trackblazer Scenario Notes

The Trackblazer brief in [`docs/MANT_TRACKBLAZER_BRIEF_REFERENCE.md`](./MANT_TRACKBLAZER_BRIEF_REFERENCE.md) changes how the bot flow should be interpreted.

Trackblazer is not primarily a "train unless a goal race appears" scenario. The flow should assume:

- checkpoint progression is driven by `Grade Points`
- race cadence is a core progression mechanic
- `Shop Coins` and item economy are part of normal decision-making
- rival races can have separate value from normal races
- repeated races create explicit fatigue risk
- the endgame is `Twinkle Star Climax`, a 3-race finale rather than a normal finals bracket

That means Trackblazer should be modeled as a race-centric scenario with training windows, not as a training-centric scenario with occasional races.

## Trackblazer Planning Boundaries

When implementing or reviewing Trackblazer support, these decision boundaries should be explicit in state and flow:

- `checkpoint window`
  Which checkpoint is active and how many Grade Points still matter in the current window.
- `race bonus state`
  Whether the run is maintaining the minimum useful race cadence.
- `shop economy`
  Current Shop Coins, important inventory, and whether the shop should be visited.
- `race fatigue state`
  Consecutive race risk and whether the bot should deliberately break race chains.
- `rival opportunity state`
  Whether a candidate race includes a rival bonus worth extra value.
- `burst training window`
  Whether the run is entering a special high-value training period, especially summer.
- `finale planning state`
  Whether race routing should consider Twinkle Star Climax profile shaping.

These are better treated as named state concepts than as loose booleans added ad hoc to action code.

## Trackblazer Suggested Sub-Flows

The following flow is a better scenario reference than simply mirroring URA/Unity:

1. Confirm stable lobby screen and detect Trackblazer.
2. Read checkpoint progress:
   Grade Points earned in the current checkpoint window and remaining threshold pressure.
3. Read race-economy state:
   Race Bonus, Shop Coins, inventory, and fatigue context.
4. Decide whether this turn belongs to a race-centric path or a training-centric path.
5. If racing:
   scan race candidates for grade point value, coin value, rival value, aptitude risk, and fatigue impact.
6. If training:
   check whether item use or a planned burst window changes the normal training value.
   For the current Trackblazer pre-action item and `Reset Whistle` reassess behavior, see [`docs/TRACKBLAZER_PRE_ACTION_ITEM_FLOW.md`](./TRACKBLAZER_PRE_ACTION_ITEM_FLOW.md).
7. Before committing:
   preview skill/shop/race clicks and preserve the reasoning in the operator console.
8. Near endgame:
   switch from ordinary checkpoint logic to Twinkle Star Climax planning/execution.

## Trackblazer Minimum State Model

Before adding more automation, these fields are reasonable candidates for the documented minimum state model:

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

This does not mean all of them must be implemented immediately. It means new OCR or action logic should map back to one of these explicit concepts.

## Recommendation For Future Refactor

If this flow becomes important across multiple scenarios, create a small shared module such as `core/runtime_phases.py` that defines:

- top-level phase constants
- documented sub-phase names
- short comments describing each boundary

Then:

- import those constants in `core/operator_console.py`
- use them in `core/skeleton.py`
- keep this document as the prose reference

That gives both code and documentation a single place to evolve from.
