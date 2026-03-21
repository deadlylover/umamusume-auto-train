# Trackblazer Pre-Action Item Flow

Canonical reference for Trackblazer training-item behavior before a training action is committed.

This document is intentionally small so it can be used by humans and AI agents as the source of truth for:

- when Trackblazer training items are planned
- what `Reset Whistle` is allowed to do
- when the bot must stop and reassess
- whether the reassess pass can add more items before training

## Scope

This applies only to the Trackblazer scenario.

It describes the current execute-mode behavior around:

- `trackblazer_pre_action_items`
- `Reset Whistle`
- post-reroll training rescans
- second-pass item planning before the final training click

## Canonical Behavior

### 1. Item planning happens after action selection

After the bot finishes Trackblazer training scan and strategy selection, it computes a pre-action item plan for the selected action.

That plan is attached to the action as:

- `trackblazer_pre_action_items`
- `trackblazer_item_use_context`
- `trackblazer_reassess_after_item_use`

This means the item plan is based on the currently selected action, not on a generic inventory-only rule.

### 2. Pre-action items are executed before the main action

In execute mode, planned Trackblazer items are applied before the final action click.

For a training action, the normal order is:

1. select pre-action items
2. confirm item use
3. if no reassess is required, perform the final training click

In `check_only` and `preview_clicks`, these remain preview steps only.

### 3. `Reset Whistle` is a hard reassess boundary

If the planned pre-action items include `Reset Whistle`, the bot must not continue straight into the previously previewed training click.

After a successful whistle use, the bot stops the current action path and returns to the main turn loop for a fresh evaluation.

Required behavior:

1. use `Reset Whistle`
2. return `reassess`
3. rescan turn state and trainings
4. run strategy again on the rerolled board
5. rebuild the pre-action item plan from that new board
6. only then preview/execute the final action

This is the key guarantee that prevents the whistle from being wasted on the old board.

### 4. The reassess pass can add more items

After a whistle reroll, the next pass is allowed to propose a different item plan than the first pass.

That means a rerolled strong training can pick up new pre-action items such as:

- burst items like `Motivating Megaphone`
- stat-matching items such as manuals, scrolls, notepads, or ankle weights
- energy items, if current policy allows them

This is not a continuation of the old item list. It is a fresh item-planning pass against the rerolled board.

### 4a. Multiple `Reset Whistle` uses are possible across passes

Current behavior allows at most one `Reset Whistle` use in a single pre-action item pass.

That happens because item planning evaluates each item key once per pass, and item execution increments each requested item once before confirm.

However, multiple whistles are possible across reassess passes:

1. first pass plans one `Reset Whistle`
2. whistle is used
3. bot returns to reassess
4. inventory and trainings are scanned again
5. if another whistle is still held and the new board is still not worth committing, the next pass may plan `Reset Whistle` again

Canonical interpretation:

- one whistle per item-use pass
- potentially more than one whistle in the same turn across repeated reassess loops
- each whistle use must be separated by a full reroll evaluation

This means "check training -> use whistle -> check training -> use whistle" is compatible with current flow structure.

### 5. Previewed clicks after a whistle are provisional

The operator console may still show the originally selected training clicks in the first preview.

Those clicks are provisional when `trackblazer_reassess_after_item_use` is true.

Canonical interpretation:

- `Rescan trainings after item use` is the last guaranteed step of the first pass
- any later training clicks shown in that preview are not guaranteed to execute
- the real committed training click must come from the second-pass evaluation

## Current Policy Notes

These are behavior notes for the current code, not a statement of ideal future policy.

### Burst items after reassess

The second pass can propose burst items, but only if the rerolled training qualifies as a committed burst training under current item-use policy.

At the moment, that gate is stricter than "a rainbow exists".

### Current multi-whistle gate

The current whistle gate does not require burst-enabling items such as:

- a megaphone
- a stat-matching ankle weight
- both together

Instead, the current gate only checks whether some follow-up fail-safe support exists, mainly:

- held energy items
- held `Good-Luck Charm`
- affordable shop energy items
- affordable shop `Good-Luck Charm`

So the current code can decide that a second whistle is allowed even when the run does not hold the burst items you would ideally want before spending more rerolls.

This is a known policy gap relative to the desired "only chain whistles when we still have meaningful burst conversion items" behavior.

### `Vita 65`

Current policy keeps a reserve of 1 `Vita 65`.

So if only 1 copy is held, the reassess pass will normally keep deferring it even when the rerolled training is strong.

### Failure reduction logic

Current energy-item logic does not explicitly reason as:

"use energy now because it will reduce failure to 0."

It evaluates energy items using the current training value, summer timing, and energy deficit rules.

## Practical Reading Guide

When reviewing a Trackblazer turn with `Action: use Reset Whistle -> recheck trainings`, read it as:

1. the first pass judged the current board not worth committing
2. the whistle is meant to create a new board
3. the bot must pause the original action path after whistle use
4. the next pass may choose a different training
5. the next pass may also choose additional training items before that training is committed

## Related References

- [`docs/MANT_TRACKBLAZER_BRIEF_REFERENCE.md`](./MANT_TRACKBLAZER_BRIEF_REFERENCE.md) — gameplay mechanics, grade points, shop coins, race fatigue, TSC
- [`docs/BOT_FLOW.md`](./BOT_FLOW.md)
- [`docs/MANT_ITEM_USE_STRATEGY.md`](./MANT_ITEM_USE_STRATEGY.md)
- [`core/trackblazer_item_use.py`](../core/trackblazer_item_use.py)
- [`core/trackblazer_shop.py`](../core/trackblazer_shop.py)
