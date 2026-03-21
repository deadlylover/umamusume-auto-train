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

`core/trackblazer_race_logic.py` uses `_WEAK_TRAINING_THRESHOLD = 35` total
raw stat gain. If the best training's total stat gain falls below this threshold,
the gate biases toward racing instead (unless summer window or no race available).

The scoring mode does not affect the race gate — the gate uses raw total stat
gains regardless of scoring mode.

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

### Future tuning ideas

- **Early-game friendship bias**: For early timeline templates, keep using
  `default` scoring mode (friendship/rainbow functions). Switch to `stat_focused`
  at the timeline point where bonds are established and stat accumulation matters.
- **Wit threshold**: Wit training gives fewer raw stats (~22 vs ~34 for others).
  Could add a wit-specific bonus or lower the race gate threshold for wit.
- **Per-phase weight sets**: Different `stat_weight_set` configs per timeline
  phase (e.g. weight guts lower in senior year when guts cap is already close).

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
