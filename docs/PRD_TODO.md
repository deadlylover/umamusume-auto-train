# PRD TODO

This document is a holding area for product and engineering improvements that
should be specified and implemented later.

## Failure OCR Stabilization

### Problem

The failure-rate UI animates and pulses. If OCR runs during a transition frame,
the bot can misread the value or miss the read entirely.

### Goal

Make failure-rate OCR resilient to transient animation states without slowing
normal operation too much.

### Proposed Direction

- Treat obviously bad failure reads as suspect instead of final.
- Retry failure OCR across multiple frames when the initial read is missing,
  low-confidence, or implausible.
- Sample across roughly 2 to 3 seconds when retry mode is triggered so the bot
  can read a stable frame instead of a pulse/jump frame.
- Accept a result using a stability rule such as majority vote, repeated-match
  confirmation, or highest-confidence match.
- Guard against false low values such as accidental `0%` reads when template
  confidence is weak or the digit crop is unstable.
- Preserve the retry captures in OCR debug output so unstable reads can be
  diagnosed after the fact.

### Notes

- This should be generic enough to support future failure-percent template
  variants and OCR heuristics.
- Prefer a targeted retry path that only activates on suspicious reads so clean
  reads stay fast.

## Trackblazer Stat Goal And Pace Planner

### Problem

Trackblazer currently lacks a dedicated stat-planning layer that answers:

- whether current stats are above or below par for the current in-game date
- whether the run is on pace toward a preset end-state stat goal
- whether upcoming mandatory long-distance races require a temporary Stamina bias
- when friendship/support growth should be preferred over raw stat optimization

The existing generic strategy system already uses `target_stat_set`,
`STAT_CAPS`, and timeline templates, but that model is not a good fit for this
scenario's needs. Trackblazer wants a more date-aware "pace vs target" system,
including hard race checks and early bond-growth windows, instead of reusing the
normal static weighting/cap path.

### Goal

Add a Trackblazer-specific planning system that:

- reads current stats and compares them against preset target curves
- shows clear above-par / below-par status with easy-to-read gradients
- injects mandatory minimum-Stamina gates for known hard races on fixed dates
- prefers friendship/support-card growth until a defined early-run cutoff
- remains separate from the current generic strategy weighting/cap system

### Proposed Direction

- Treat this as a new Trackblazer-only planner, not an extension of the current
  generic `training_strategy` stat-weight/cap model.
- Reuse the existing stat OCR/state surface as the read source where possible,
  then add Trackblazer-specific interpretation on top of it.
- Add a preset config surface for Trackblazer stat goals, likely as a date-aware
  target curve or milestone table instead of a single flat target number.
- For each turn, compare current stats against:
  - the final desired stat profile
  - the expected value for the current date
  - any active hard-race minimums that must be met before a fixed race date
- Surface that comparison in the operator/debug console with strong visual
  gradients so it is obvious which stats are ahead, on pace, or behind.
- Add a Trackblazer-specific "required Stamina floor" layer for predetermined
  hard races, for example long-distance races such as Tenno Sho (Spring) 3200m,
  so training can bias toward Stamina when the floor is not yet satisfied.
- Keep race-specific minimums date-driven and explicit, not heuristic-only.
- Add a date-gated early-game preference for friendship/support-card growth,
  with the default cutoff around Classic Year Early/Late Jun, just before the
  Classic summer window begins in July.
- Let the planner hand off a summarized bias/state signal into Trackblazer
  decision-making rather than rewriting the entire generic strategy stack at
  first.

### Notes

- This should live alongside the Trackblazer scenario logic and policy modules,
  not as another small exception inside generic `core/strategies.py`.
- The config surface should be separate from current generic stat weightings and
  stat caps; Trackblazer should be able to ignore those values entirely when
  this planner is active.
- "Above/below par" should be tied to game date, not only to absolute current
  stat values.
- The friendship-growth preference is conceptually similar to the current
  strategy timeline behavior, but it should be expressed here as Trackblazer
  scenario policy with an explicit cutoff date.
- This TODO should eventually align with Trackblazer observability work so the
  console can show the planner state, deficits, active Stamina gates, and
  current growth-vs-stats phase clearly.

## Trackblazer Race Gate Follow-up

### Problem

The current Trackblazer race-selection and gating flow is only partially wired.
Some payloads and template registrations exist, but important parts of the
decision path still rely on scaffolding or are not consumed in execution yet.

### Goal

Finish the race gate so it uses live UI signals where needed, distinguishes
strong races from marginal races, and enforces the consecutive-race warning in
the actual execution path instead of only surfacing a review payload.

### Follow-up TODOs

- `race_g2` / `race_g3` are registered but unused in decision logic; add grade
  detection from the live race-list UI.
- Add a normal-time rival button indicator; only
  `summer_rival_race_button` exists today, so non-summer rival detection can
  miss.
- Wire the consecutive-race warning into execution flow; the
  `race_entry_gate` payload is currently built for the operator console but not
  consumed by the executor.
- Split warning-policy handling for marginal races versus strong
  `should_race` cases; today all races that pass the gate end up as
  `continue_to_race_list`, but checkpoint/fatigue pressure may require a finer
  distinction.
- Replace or harden schedule-backed race availability; `_detect_race_options`
  currently uses `constants.RACES` instead of the live UI, which carries
  schedule/aptitude mismatch risk.

### Notes

- This should stay aligned with the Trackblazer race logic notes in
  `docs/TRACKBLAZER_RACE_LOGIC.md`, but the backlog owner should be this TODO
  document.
- The live-UI grade/rival signals and the consecutive-race gate should be
  treated as execution correctness work, not just operator-console
  observability.

## Trackblazer Megaphone Active-State Detection

### Problem

Trackblazer megaphones last multiple turns, but the current inventory/item-use
flow does not explicitly detect whether a megaphone buff is already active.
That creates overlap risk:

- the bot can attempt to reuse a megaphone while its previous buff is still
  running
- the inventory increment button may be greyed out / disabled for an item that
  cannot currently be used, but we do not have an asset for that state
- when the relevant megaphone is no longer present in inventory, the bot cannot
  infer active state from inventory alone

The desired behavior is waterfall-aware:

- if a lower-tier buff is active, a stronger megaphone should still be allowed
  to override it
- if a stronger buff is active, the weaker megaphone should be blocked

### Goal

Add reliable active-state detection for Trackblazer megaphones so pre-action
item planning and execution avoid invalid or wasteful overlapping uses while
still allowing valid upgrades.

### Proposed Direction

- Capture a greyed-out / disabled increment-button asset for the Trackblazer
  inventory item row and wire it into inventory-control detection.
- Treat the greyed increment state as a first-class execution signal, not just
  the absence of a normal increment match.
- Capture buff-icon assets for active megaphone effects so the bot can detect
  the currently active training-bonus state even when the corresponding item is
  absent from inventory.
- Extend Trackblazer inventory/state payloads to surface:
  - whether a megaphone row is actionable
  - whether its increment button is disabled
  - which megaphone buff icon, if any, is active on the current turn
- Add item-use policy logic that encodes waterfall override rules between
  megaphones, so stronger buffs can replace weaker ones but not vice versa.
- Prefer explicit live UI detection over deduction from remaining inventory
  quantity alone.

### Notes

- This should land in the Trackblazer item-use path, not as a generic inventory
  scanner heuristic.
- Relevant implementation points will likely include
  `scenarios/trackblazer.py`, `core/state.py`, `core/trackblazer_item_use.py`,
  and `utils/constants.py`.
- The operator console should eventually expose the detected active megaphone
  state and the reason an increment button is considered blocked.
