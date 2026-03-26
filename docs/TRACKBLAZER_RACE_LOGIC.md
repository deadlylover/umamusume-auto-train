# Trackblazer Race Logic

## Purpose

This document describes the current first-pass Trackblazer race-vs-training gate and the places to edit when live testing shows it should change.

The implementation is intentionally heuristic. It is meant to be easy to iterate, not final.

## Code Entry Points

- `core/trackblazer_race_logic.py`
  - `evaluate_trackblazer_race(state_obj, action)` is the main decision helper.
- `core/skeleton.py`
  - calls the helper after `strategy.decide(...)`
  - stores the result on `action["trackblazer_race_decision"]`
  - can override training to race
  - can revert a race fallback back to training
- `core/operator_console.py`
  - shows the decision in the compact summary and planned-actions panes

## Current Policy

The race schedule (`constants.RACES`) is only used for **mandatory** checks (Race Day, G1). For all optional race decisions, the **rival indicator on the race button** (visible on the lobby screen via `SCREEN_BOTTOM_BBOX`) is the source of truth. The pre-filtered schedule is not consulted for optional races because it can produce false negatives — the rival button on screen is ground truth for whether a race is actually available this turn.

Current rule order:

1. `Race Day` is mandatory (schedule-driven).
2. If a `G1` is available on the current date, race it (schedule-driven).
3. Check for the rival race indicator on the lobby race button (`SCREEN_BOTTOM_BBOX`).
4. If no rival indicator is visible, do not race.
5. If rival is visible but energy < 5%, do not race.
6. In `stat_focused` mode, if the selected training score is `>= 40`, keep training instead of taking the optional rival race.
7. In summer with rival visible: only race if training is weak (< `35` total stats).
8. Outside summer with rival visible and weak training: race.
9. Outside summer with rival visible and adequate training: still race unless the score check above already kept the training turn.

The rival indicator check is deferred until after mandatory checks, so Race Day / G1 dates skip the screenshot cost.

When the gate says race, `scout_rival_race()` opens the actual race list and verifies 2-aptitude match inside. If no suitable rival is found, the existing fallback in skeleton.py reverts to the training action — so backing out is safe.

Working policy notes from live testing:

- rival racers appear to be graded races only (`G1` / `G2` / `G3`)
- if a rival race is present and aptitudes are acceptable, it is usually safe to prioritize clicking the rival entry
- the `summer_rival_race_button` asset works for both summer and normal lobby (same icon, slightly repositioned)

## Current Signals

The current scaffold uses only signals that already exist or are cheap to read:

- `selected training total stats`
  - summed from `action["training_data"]["stat_gains"]`
- `selected training score`
  - taken from `action["training_data"]["score_tuple"][0]`
  - in `stat_focused` mode this includes the Trackblazer bond boost
- `summer window`
  - based on the timeline label
- `energy level`
  - from `state_obj["energy_level"]` / `state_obj["max_energy"]`; minimum 5% to race
- `mandatory race schedule (G1 / Race Day only)`
  - inferred from `constants.RACES` for the current date after aptitude filtering
  - only used for Race Day and G1 mandatory checks; not used as a gate for optional races
- `rival indicator`
  - cheap lobby pre-check from `scenarios/trackblazer.py::check_rival_race_indicator()`
  - scans `SCREEN_BOTTOM_BBOX` for the VS icon on the race button
  - this is the primary signal for optional race availability

This does not yet read the live race list to determine race grade from UI assets.

## Current Race Assets

Already registered:

- `assets/trackblazer/summer_rival_race_button.png`
- `assets/trackblazer/rival_racer.png`
- `assets/trackblazer/race_recommend_2_aptitudes.png`
- `assets/trackblazer/race_warning_consecutive.png`
- `assets/trackblazer/race_g3.png`

Captured but not yet wired into decision logic:

- `assets/trackblazer/race_g2.png` — registered in `TRACKBLAZER_RACE_TEMPLATES` but not consumed by the race gate yet

Still expected later:

- normal-time rival button indicator
- optional `race_g1` for UI validation

## Consecutive Race Warning Flow

The consecutive-race warning is not a race-list signal. It appears immediately after clicking the lobby `Races` button and before the race list opens.

This applies in both:

- normal lobby
- summer lobby

Current observed behavior:

- trigger: click the lobby race button
- popup: warns that taking this action would become 3 consecutive races
- buttons:
  - `ok` = continue into the race list
  - `cancel` = return to the lobby

Implication for flow design:

1. decide whether racing is worth attempting from the lobby
2. click the race button
3. if `race_warning_consecutive` appears, make a second decision:
   - continue with `ok`
   - back out with `cancel`
4. only then proceed to race-list scouting / selection

This means the warning should be modeled as a lobby-to-race-list confirmation gate, not as part of race-row selection.

### Planned Policy For This Warning

Initial intended handling:

- forced `G1` should accept the warning
- marginal optional races should usually cancel
- strong rival / graded value can justify accepting
- dead-turn fallback races can justify accepting when trainings are poor

Still TODO before this is trustworthy:

- checkpoint pressure
- fatigue-aware race cadence
- item/status-based exceptions

## Decision Payload

`evaluate_trackblazer_race(...)` returns a dict with:

- `should_race`
- `reason`
- `training_total_stats`
- `is_summer`
- `g1_forced`
- `prefer_rival_race`
- `race_tier_target`
- `race_name`
- `race_available`
- `rival_indicator`
- `race_tier_info`

The payload is attached to the selected action as `trackblazer_race_decision`.

## Current Limitations

### Race tier detection is schedule-based for mandatory races only

The schedule (`constants.RACES`) is only consulted for Race Day and G1 mandatory checks. Optional race availability is determined by the rival indicator on screen, not the schedule.

Remaining schedule-based limitations:

- G1 detection still relies on the schedule, not a live UI grade icon
- the helper does not yet verify race grade from the race list screen itself
- it does not yet prove from the live row that a rival race is graded (relies on the scout finding 2-aptitude match)

## Race List Recognition Notes

The live race list appears to show roughly two rows before scrolling becomes necessary, and partial-row occlusion is expected.

Current rough layout from a live capture on `Senior Year Early Jul / 12`:

- `RACE_LIST_BOX_BBOX = (33, 861, 740, 1253)`
- visible `G3` matches at about:
  - region-relative `(211, 28)`
  - region-relative `(209, 194)`
- visible `RIVAL RACER!` matches at about:
  - region-relative `(499, 166)`
  - region-relative `(498, 332)`
- visible `2 aptitudes` matches at about:
  - region-relative `(575, 238)`
  - region-relative `(576, 273)`

Useful implications:

- row pitch looks to be about `166px`
- the `rival_racer -> 2 aptitudes` offset already used in code is still about right:
  - `dx ~= 77`
  - `dy ~= 71`
- the race grade badge is far left in the row, while the rival marker and aptitude stars live much farther right

### Occlusion Rule

Do not assume all row features are visible at the same time.

In particular:

- the race row can be partially visible near the top or bottom of the race list
- you may see `rival_racer` without the aptitude stars below it
- you may see a grade badge without the full right side of the row

Planned handling for this:

1. treat `rival_racer` as a row anchor
2. try to pair it with `race_recommend_2_aptitudes`
3. if the row is near the bottom edge and aptitude is missing, scroll once and rescan before rejecting the rival
4. once `race_g2` / `race_g3` are added, associate grade badges to the same row band rather than assuming the whole row is visible

### Checkpoint pressure is not modeled

The helper does not yet use:

- Grade Points
- checkpoint targets
- Race Bonus pressure

When those fields become trustworthy, they should be added to `core/trackblazer_race_logic.py` as another early decision branch.

### Fatigue is not modeled

The helper does not track consecutive races or the 3+-race penalty risk yet.

### TSC is not modeled

Twinkle Star Climax should eventually become its own branch or sub-phase. Right now it is not treated specially.

## How To Tune It

Current tuneables live at module scope in `core/trackblazer_race_logic.py`:

- `_WEAK_TRAINING_THRESHOLD = 35`
- `_STRONG_TRAINING_SCORE_THRESHOLD = 40`
- `_MIN_RACE_ENERGY_PCT = 0.05` (5% minimum energy to consider racing)
- `_SUMMER_WINDOWS`

If live testing shows we need more operator-level tuning, move these into config later. For now, keeping them local makes iteration faster.

## How The Main Loop Uses It

After `strategy.decide(...)`:

1. `core/skeleton.py` runs the Trackblazer race gate.
2. The decision is stored on the action for review/debug output.
3. If the gate says race and the current action is training, the action is converted to `do_race`.
4. If the selected training score is strong enough in `stat_focused` mode and the current action is rest, the action is promoted back to `do_training`.
5. If the gate provides a concrete race name, it is attached to the action.
6. If the gate prefers a rival race, the later rival scout still runs and can fall back cleanly if no rival race is actually found.
7. If the gate says train and the current race action was only a fallback, the action is reverted to the original training choice.

## Review / Debug Visibility

The operator console now shows:

- `Race Gate: race/train`
- target tier when present
- selected race name when present
- training total stat gain
- training score when present
- rival and summer flags
- the human-readable decision reason

The relevant sub-phase is:

- `evaluate_trackblazer_race`

## Recommended Next Iterations

Add the following in roughly this order:

1. Add normal-time rival button indicator template.
2. Add `race_g2` and wire grade detection from the live race list.
3. Use `race_warning_consecutive` as a specific branch instead of generic `cancel/ok` popup handling.
4. Checkpoint / Grade Point pressure.
5. Consecutive-race fatigue tracking.
6. TSC-specific routing.
7. Config-driven thresholds if live tuning becomes frequent.

## Live Test Notes

While testing:

- summer should mostly stay training-first (rival only overrides when training is weak)
- weak training outside summer should bias toward rival race when available
- `G1` should be treated as forced (schedule-driven)
- rival indicator on the lobby race button is the sole signal for optional race availability
- the scout verifies aptitude inside the race list; if no 2-aptitude rival found, fallback to training

If a live run looks wrong, capture:

- turn label
- selected training and total projected stats
- race gate output from the console
- whether the rival indicator was present
- whether the chosen race grade from the actual UI matched the schedule-backed assumption
