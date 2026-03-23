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
  committed actions but before the stable lobby is back. This is the **unified
  resolver** — the single owner for ALL post-action follow-up screens including
  post-race result chains. See **Post-Action Resolution** below for the full
  branch table.
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
- `resolve_event_choice`
- `resolve_shop_refresh_popup`
- `resolve_scheduled_race_popup`
- `resolve_consecutive_race_warning`
- `state_invalid_retry`
- `action_failed_retry`
- `return_to_lobby`

## Post-Action Resolution

`_resolve_post_action_resolution()` in `core/skeleton.py` is the **unified resolver** that runs after every committed action (training, race, rest, recreation, infirmary). It replaced the former dual-resolution system where `start_race()` in `actions.py` owned its own post-race lobby loop AND skeleton.py ran a second resolution pass.

### Two-tier safety net

1. **Resolver** (`_resolve_post_action_resolution`) — runs for up to `max_wait` seconds (45s for races, 20s for everything else). Handles structured popups with logging and operator console updates.
2. **Generic lobby scan** (`career_lobby()` main loop) — if the resolver times out, control returns to the lobby scan which has its own handlers for every template. This is the ultimate fallback — nothing truly gets stuck, but unhandled branches add up to `max_wait` seconds of idle timeout.

### Resolver loop priority order

Each iteration of the resolver checks in this order:

1. **Stable lobby** — template-match against `STABLE_CAREER_SCREEN_ANCHORS` (tazuna_hint, training_btn, rest_btn, recreation_btn, races_btn, details_btn, details_btn_2). If any anchor matches → done, return success.
2. **Event choice** — `select_event()` handles support events, character events, and trainee events.
3. **Trackblazer shop refresh popup** — `_handle_trackblazer_shop_refresh_popup()` detects the refresh dialog, dismisses it, and queues a deferred shop check (only if dismiss click succeeds).
4. **Trackblazer scheduled race popup** — `_handle_trackblazer_scheduled_race_popup()` detects the scheduled race banner, clicks through race entry, handles the consecutive-race warning, calls `start_race()`, and returns. The outer resolver loop then handles the scheduled race's own post-race screens in subsequent iterations.
5. **Generic advance buttons** — `_generic_post_action_return_to_lobby_step()` tries each of these in order: next2, next, ok_2_btn, retry, close, view_results, back, cancel. The cancel button is guarded by a clock_icon check (skipped if clock_icon is present, meaning lost-race screen).
6. **Idle safe-space tap** — after 3 consecutive loops with no button matched, taps safe space to try to advance any unknown screen.
7. **Timeout** — logs a warning and returns `True`, falling through to the lobby scan.

### All post-action branches

| Branch | Resolver handler | Lobby scan fallback | Notes |
|--------|-----------------|---------------------|-------|
| **Event choice** (support / character / trainee) | `select_event()` | `select_event()` | Covered in both tiers |
| **TB shop refresh popup** | `_handle_trackblazer_shop_refresh_popup()` | Same function + non-TB cancel fallback | Covered in both tiers |
| **TB scheduled race popup** | `_handle_trackblazer_scheduled_race_popup()` | — | Resolver-only; lobby scan has no equivalent |
| **Post-race result screens** (view_results, next, next2) | Generic advance templates | next / next2 in cached_templates | Covered in both tiers |
| **Post-race concert** (landscape close) | N/A — handled inside `start_race()` before resolver starts | — | Pre-resolver |
| **Retry prompt** (failed race retry) | Generic advance — "retry" | retry in cached_templates | Covered in both tiers |
| **Cancel button** (with clock_icon guard) | Generic advance — "cancel" with clock_icon skip | cancel with clock_icon check | Covered in both tiers |
| **OK confirmation dialog** | Generic advance — "ok_2_btn" | ok_2_btn in cached_templates | Covered in both tiers |
| **Close button** (generic overlay) | Generic advance — "close" | close_btn in unity_templates | Covered in both tiers |
| **Back button** | Generic advance — "back" | — | Resolver-only |
| **Inspiration** | **Not covered** | inspiration_btn in cached_templates | Lobby scan fallback only |
| **Claw machine** | **Not covered** | claw_btn in cached_templates | Lobby scan fallback only |
| **TB year-end screen** | **Not covered** — likely dismissed by generic next/close/ok_2_btn | Same generic buttons | Needs verification; auto-handled by legacy flow previously |
| **URA finale screens** | Generic next/close buttons | Same | Likely sufficient |
| **Unity Cup popup** | **Not covered** | unity_cup_btn / unity_banner_mid_screen | Lobby scan fallback only |
| **Non-TB shop sales popup** | Generic cancel button | cancel in cached_templates | Covered |
| **Safe-space tap** | After 3 idle loops | — | Resolver-only fallback |
| **Timeout** | Returns to lobby scan | Picks up from there | Safety net |

### Known gaps (handled by lobby scan fallback)

These branches are not recognized by the resolver. They will cause idle loops until timeout, then the lobby scan picks them up:

- **Inspiration** — rare during post-action; low impact.
- **Claw machine** — rare during post-action; low impact.
- **Unity Cup popup** — only relevant in Unity scenario; the resolver is most exercised in Trackblazer.
- **TB year-end screen** — appears at the end of each year in Trackblazer. The generic advance buttons (next/close/ok_2_btn) likely handle it, but this has not been explicitly verified post-unification.

### Debugging post-action stalls

When the bot appears stuck after an action:

1. Check the operator console — the resolver logs every iteration with `sub_phase`, `popup_type`, and `reasoning_notes`.
2. Look for `[POST_ACTION] Timed out` in the terminal — this means the resolver exhausted its budget and fell through to the lobby scan.
3. If the lobby scan also can't resolve, `non_match_count` will climb toward 20, after which the bot quits.
4. To identify the stuck screen: the resolver's `_update_post_action_resolution_snapshot` records `anchor_counts` on each loop — this shows which lobby anchors were visible (or not) and what the resolver tried to click.

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
