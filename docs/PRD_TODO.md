# PRD TODO

This document is a holding area for product and engineering improvements that
should be specified and implemented later.

## Trackblazer Post-Action Resolution Flow State

### Problem

The bot still treats too much of the "after an action, before stable lobby"
window as generic lobby cleanup.

That is the wrong abstraction for Trackblazer because important scenario
screens can appear in that resolution window:

- shop refresh / shop sale popups
- scheduled race available popups
- consecutive-race warning and similar gates
- result / reward / follow-up scenario dialogs that are not just generic
  `cancel` / `next` recovery noise

Today those screens are easy to accidentally handle in the wrong place:

- they get mixed into generic lobby scanning branches
- they compete with fallback "press cancel / next / back to get to lobby"
  behavior
- new popup assets do not have a canonical home, so adding them risks more
  one-off branches

### Goal

Add a documented flow boundary for `post_action_resolution` so scenario popups
that happen after training/races/events are handled before the bot falls back
to generic "return to lobby" cleanup.

### Proposed Direction

- Treat the period after action execution and before stable lobby confirmation
  as its own domain flow, not as plain `lobby_scan`.
- Add a canonical runtime sub-phase family for this window, for example:
  - `post_action_resolution`
  - `resolve_post_action_popup`
  - `resolve_shop_refresh_popup`
  - `resolve_scheduled_race_popup`
  - `return_to_lobby`
- Route scenario-aware popup checks through this boundary first.
- Keep generic fallback clicks such as `cancel`, `next`, `back`, and close
  buttons as a final recovery layer only after known scenario popups were
  checked and declined.
- When a popup implies deferred work rather than immediate execution, let the
  resolution flow set an explicit pending flag for later controlled handling.
  Example:
  - shop refresh / sale popup => dismiss popup, mark `shop_check_pending`
  - scheduled race popup => mark or enter the race flow using a named branch,
    not a lobby-template side path
- Document which popup classes belong to:
  - immediate resolution
  - deferred scenario work
  - generic recovery

### Notes

- This should align with `docs/BOT_FLOW.md` and
  `docs/PRD_TRACKBLAZER_FLOW_UPDATES.md`, but the backlog owner should be this
  TODO entry.
- The main requirement is architectural: new assets like shop sale, shop
  refresh, scheduled race available, and future Trackblazer popups need a
  predictable insertion point.
- The fallback "get back to lobby no matter what" behavior is still required,
  but it should be downstream of scenario-aware resolution, not competing with
  it.

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

## Stat Goal Extrapolated Training

### Problem

Training decisions are currently turn-local: the bot picks the best training for
*this turn* based on stat weights and scores. It has no awareness of the
career arc — how many trainable turns remain, what the expected stat income is
from those turns and upcoming races, or whether the run is on pace to hit a
specific end-state stat target such as 1100 Speed for a 3-star spark.

This means the bot can leave value on the table in the final stretch by
picking marginally higher-score trainings that do not address a stat deficit,
or by not recognising that a weaker-looking training is the only remaining
path to a goal.

### Goal

Add an expected-value extrapolation layer that projects final stats from the
current state and adjusts training weights accordingly.

### Proposed Direction

- **Race agenda awareness** — read the in-game race agenda (requires future
  implementation) or fall back to the config JSON race list. Knowing which
  upcoming turns are races vs trainable is the foundation.
- **Race stat reward estimates** — approximate stat gains from G1/G2/G3 races
  in Trackblazer. Needs data collection; rough constants are fine initially.
- **Support card modelling** — allow the user to select which support cards are
  in the deck. From each card's specialty and friendship level, estimate the
  probability of it appearing on each training type and the stat contribution
  it brings. This gives an expected-value distribution per training per
  remaining turn.
- **Remaining turn budget** — subtract races and expected rest turns (projected
  from current energy trend) from the remaining calendar to get an effective
  training count.
- **Par tracking** — given a preset stat target (e.g. 1100 Speed), compare
  current stats against the expected value at this game date. Surface
  above-par / below-par clearly.
- **Training weight adjustment** — when a stat is below par with few trainable
  turns left, boost the weight for that stat's training even if another
  training has a higher raw score. Example: 4 trainable turns remaining,
  Speed at 1020 vs 1100 target → speed training gets boosted over a
  technically higher-scoring power training.

### Notes

- Low priority — this is an idea capture, not an imminent implementation.
- Prerequisites: race agenda reading or reliable config-based race schedule;
  support card selection UI (web UI "Skeleton" or dedicated tab).
- This complements the existing "Trackblazer Stat Goal And Pace Planner" TODO
  (above) but focuses specifically on support-card expected-value modelling
  and remaining-turn extrapolation rather than pace curves and hard-race
  Stamina gates. The two may merge during implementation.
- Relevant touchpoints: `core/strategies.py`, `core/trainings.py`,
  `utils/constants.py` (timeline/race data), Trackblazer scenario policy.

## Stat History Logging For Run Analytics

### Problem

There is no persistent record of stat progression across bot runs. Every run's
stat readings disappear when the session ends. This makes it impossible to
answer questions like "what is the average Speed at Senior Early Oct across
the last 20 runs?" or "are recent config changes producing better results?"

### Goal

Log every stat reading together with its game date/year to a persistent file so
that run performance can be tracked and averaged across many runs.

### Proposed Direction

- Each time the bot reads stats (via `collect_main_state()`), also read the
  current in-game date/year and append a record to a persistent log file.
- Record format should include at minimum: run ID or session timestamp, game
  date (year + period, e.g. "Classic Early Jul"), and all five stat values
  (Speed, Stamina, Power, Guts, Wit).
- Use a simple append-friendly format — JSONL (one JSON object per line) or
  CSV — so the file can be consumed by scripts, notebooks, or a future web UI
  analytics tab.
- File location: something like `data/stat_history.jsonl` or configurable via
  `config.json`.
- A run/session ID should be assigned at bot start so individual runs can be
  grouped when computing averages.
- Optional: also log energy, mood, scenario name, and active training template
  for richer analysis.

### Notes

- Low priority — idea capture.
- The stat and date OCR already exist in `core/state.py`; this is mainly a
  persistence/write concern, not new OCR work.
- Keep the write path lightweight (append, no locking beyond basic file I/O)
  so it does not slow the main loop.
- Pairs well with "Stat Goal Extrapolated Training" and "Stat Goal And Pace
  Planner" — the historical data could seed the expected-value curves and
  par targets those features need.

## Trackblazer Post-Summer Burst Commitment Tuning

### Problem

The current Trackblazer "committed burst training" definitions are still tuned
mostly around the two summer burst windows.

That makes sense during Classic/Senior summer, but after both summer windows
are over the policy can stay too conservative about spending burst items:

- megaphones can keep waiting for an overly ideal burst board
- stat-matching ankle weights can also be held too long
- late-run value can be stranded because the gate still behaves like it is
  preserving summer-only opportunities

### Goal

Loosen committed-burst thresholds after the two summer windows are finished so
the bot becomes more willing to spend megaphones and stat-matching ankle
weights on strong post-summer trainings.

### Proposed Direction

- Audit the current committed-burst gates in `core/trackblazer_item_use.py`,
  especially the defer paths that currently wait for "a committed burst
  training".
- Keep the stricter burst definitions during the two summer windows, but add a
  more liberal post-summer policy phase once Senior Late Aug has passed.
- In that post-summer phase, allow strong high-value trainings to qualify more
  easily for megaphone and ankle-weight usage even when the board is not a
  peak summer-style setup.
- Revisit whether rainbow/support-heavy turns, large matching-stat gains, or
  strong late-run score spikes should be sufficient to count as "committed"
  after summer.
- Surface the active burst-policy phase/reason in review output so it is clear
  why items were deferred or committed.

### Notes

- This is item-use policy tuning, not inventory scanning or execution
  correctness work.
- Relevant references:
  `docs/TRACKBLAZER_PRE_ACTION_ITEM_FLOW.md`,
  `docs/MANT_ITEM_USE_STRATEGY.md`, and `core/trackblazer_item_use.py`.
