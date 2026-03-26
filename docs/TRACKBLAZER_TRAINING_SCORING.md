# Trackblazer Training Scoring Strategy

Canonical reference for how the bot selects trainings during Trackblazer (MANT) runs.
Paired with `TRACKBLAZER_RACE_LOGIC.md` (race-vs-training gate) and
`TRACKBLAZER_PRE_ACTION_ITEM_FLOW.md` (items before training execution).

## Scoring Modes

The operator console exposes a **Scoring** toggle (Trackblazer-specific) with two modes:

### `default` — Timeline Template Function

Uses whatever training function the timeline assigns for the current game phase
(e.g. `rainbow_training`, `meta_training`, `most_stat_gain`). These functions
prioritize supports, friendship progression, or rainbow count — stats are
secondary or absent from the score.

**When this is appropriate:** Early game friendship building, scenarios where
support composition matters more than raw stat gains.

### `stat_focused` — Stat Weight Training

Function: `stat_weight_training` in `core/trainings.py`.

**Score formula:**

```
score = sum of (gain * weight) for each non-capped visible stat
```

Weights come from the `stat_weight_set` in config. Default values:
`{spd: 1, sta: 1, pwr: 1, guts: 1, wit: 1, sp: excluded}`

Supports and rainbows are **not** counted separately — they already affect the
stat gain numbers shown on screen (more supports = higher gains displayed).
Counting them again would double-count their contribution.

**Key properties:**
- Pure stat-driven ranking — the training with the highest weighted stat total wins
- A weight of 1 means full value, 0.5 means half value
- Skill points (`sp`) are excluded so only main stats drive selection
- Priority index is used as tiebreaker when scores are equal

**When this is appropriate:** Mid-to-late game when raw stat accumulation matters
more than friendship building. Especially good in Trackblazer because items
(ankle weights, megaphones) amplify the chosen training — pick the highest-stat
training, then boost it with items.

## Example: Why Stat Focused Matters

Given this training board:

| Training | Stats | Supports | Score (rainbow_training) | Score (stat_focused) |
|----------|-------|----------|-------------------------|---------------------|
| spd      | +20 spd, +11 pwr = 31 | 2 | **3.675** (wins) | 20×1 + 11×0.8 = **28.8** |
| sta      | +25 sta, +9 guts = 34  | 1 | 0.978 | 25×1 + 9×0.5 = **29.5** (wins) |
| wit      | +17 wit, +5 spd = 22   | 1 | 1.105 | 17×1 + 5×1 = **22.0** |

With `rainbow_training`, speed wins because it has 2 supports. With
`stat_focused`, stamina wins because it has more total weighted stats. The user
can then apply stamina ankle weights + motivating megaphone to amplify that
training further.

## Interaction with Race Gate

`core/trackblazer_race_logic.py` still uses `_WEAK_TRAINING_THRESHOLD = 35`
total raw stat gain as the weak-training fallback. In `stat_focused` mode it
also checks the actual training score. If the selected training score is
`>= 40`, the gate keeps the training turn even when a rival race is visible.

That means the support-bond boost from `stat_weight_training()` can now keep a
good turn on training instead of sending it into a marginal rival race.

## Interaction with Energy Management

`evaluate_training_alternatives()` in `core/strategies.py` still runs after
scoring. It can override the training choice with wit training (for energy
recovery), rest, or recreation based on energy state. This applies regardless
of scoring mode.

Wit training gets evaluated for energy value (5 base + 4 per rainbow friend)
independently of its stat score. So even though `stat_focused` ranks wit lower
on raw stats, the energy management system can still select it when appropriate.

## Tuning Handles

These are the values that can be adjusted to refine behavior:

| Handle | Location | Current | Purpose |
|--------|----------|---------|---------|
| `stat_weight_set` | `config.json` templates | spd:1 sta:1 pwr:1 guts:1 wit:1 | Per-stat multiplier for gains |
| `_WEAK_TRAINING_THRESHOLD` | `trackblazer_race_logic.py` | 35 | Total raw stats below which racing is preferred |
| `_STRONG_TRAINING_SCORE_THRESHOLD` | `trackblazer_race_logic.py` | 40 | Stat-focused score at or above which training is preferred |
| `bond_boost_enabled` | `bot.py` runtime toggle | True | +10/+15 per blue/green friend on training |

### Bond Boost

Toggle: **Bond boost** checkbox in Stat Weights window (default: on).
Runtime state: `bot.trackblazer_bond_boost_enabled`.

Adds a flat score bonus for each blue or green friendship-level support card
present on a training. The goal is to prioritise raising bonds to orange
(yellow) in early game, since orange friends unlock rainbow training bonuses.

| Training | Per-friend bonus |
|----------|-----------------|
| wit      | +15             |
| all others | +10           |

Wit gets a higher bonus because it costs no energy to train, making it a
low-cost way to raise friendship when the stat gains are otherwise weak.

The boost only applies to blue and green friends (not gray, yellow, or max).
Once a friend reaches orange/max, they no longer contribute a bond boost —
they already provide rainbow stat bonuses which are reflected in the visible
gains.

**Cutoff:** The bond boost is only active up to and including a configurable
timeline turn (default: `Classic Year Early Jun` — just before first summer).
After the cutoff, pure stat-weight scoring takes over. The cutoff is
adjustable via the "Active until" dropdown in the Stat Weights window.

### Future tuning ideas

- **Per-phase weight sets**: Different `stat_weight_set` configs per timeline
  phase (e.g. weight guts lower in senior year when guts cap is already close).
- **Bond boost scaling**: The current flat +10/+15 is a simple starting point.
  Could scale by friendship level (blue > green since blue has more to gain)
  or reduce the bonus as game progresses.

## Code References

| File | What |
|------|------|
| `core/trainings.py` :: `stat_weight_training()` | The scoring function |
| `core/strategies.py` :: `Strategy.get_action()` | Where scoring mode override is applied |
| `core/bot.py` :: `trackblazer_scoring_mode` | Runtime state for the toggle |
| `core/operator_console.py` | Scoring mode radio buttons |
| `core/skeleton.py` :: `_score_training_for_display()` | Display scoring for filtered trainings |
| `core/skeleton.py` :: `_get_training_filter_settings()` | Filter settings for the new function |
| `core/trackblazer_race_logic.py` | Race-vs-training gate (independent of scoring) |
