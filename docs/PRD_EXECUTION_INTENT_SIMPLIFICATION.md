# PRD: Execution Intent Simplification

## Status

Implemented.

The current branch now uses the simplified two-mode model in practice:

- `check_only`
- `execute`

Legacy `preview_clicks` input is normalized to `check_only` for migration compatibility, but it is no longer exposed as a user-facing mode.

## Summary

Simplify operator-console execution intent from three user-facing modes:

- `check_only`
- `preview_clicks`
- `execute`

to two user-facing modes:

- `check_only`
- `execute`

`check_only` becomes the canonical review/debug/play-along mode:

- bot scans the turn
- bot shows OCR/debug and planned actions
- bot pauses for review
- pressing `Continue (F2)` executes that one turn
- the intent remains `check_only` for the next turn

`execute` remains the normal commit mode.

`preview_clicks` should be deprecated from the UI and treated as legacy input that aliases to `check_only` during migration.

## Why This Exists

The current three-mode model creates confusion without providing enough value:

- `check_only` already shows the planned action, OCR evidence, and planned clicks
- `check_only` now supports one-turn walkthrough execution via `Continue`
- `execute` covers normal unattended play
- `preview_clicks` is rarely used and is easy to misunderstand once `check_only + Continue` exists

The result is avoidable ambiguity about:

- what `Continue` should do
- which non-execute mode is the "real" debugging mode
- whether preview behavior differs materially from review behavior

The product should optimize for the workflow actually being used: review the turn, optionally commit one turn, then review the next turn.

## Product Goal

Reduce the execution-intent model to the minimum set that matches real usage and current bot behavior:

- `check_only` for review/walkthrough/debug
- `execute` for normal committing behavior

## Non-Goals

- Reworking the full semi-auto architecture
- Removing internal planned-click generation or OCR debug payloads
- Changing the underlying phase/sub-phase model
- Replacing the operator console
- Rewriting Trackblazer planning logic

## User Stories

- As a user, I can leave the bot in `check_only` and step through turns one at a time.
- As a user, I do not need to remember the difference between two different preview-style modes.
- As a developer, I can debug OCR and planning issues using one clear review mode.
- As a developer, I can keep old config/runtime values working during migration.

## Problem Statement

Today the repo exposes three execution intents, but only two workflows matter in practice:

1. review without permanent mode switching
2. execute normally

`preview_clicks` no longer has a strong standalone reason to exist because `check_only` already provides:

- full state scan
- visible planned clicks
- reasoning/debug inspection
- pause-before-commit
- one-shot execution via `Continue`

Keeping `preview_clicks` in the UI increases operator error risk and documentation burden.

## Current Baseline

Current behavior in the branch:

- `check_only` pauses at confirmation and allows one-shot execute via `Continue`
- `execute` commits normally
- `preview_clicks` remains in the runtime model and some docs/history, but is not part of the intended day-to-day workflow

Trackblazer execute behavior now includes:

1. planned shop purchases
2. inventory refresh
3. pre-action item use
4. reassess if item policy requires it
5. final action execution

That walkthrough behavior is already good enough to make `preview_clicks` unnecessary as a separate user-facing choice.

## Functional Requirements

### 1. Two User-Facing Intents

The operator console must expose only:

- `check_only`
- `execute`

Requirement:

- remove the `preview_clicks` radio button from the console UI

### 2. Legacy Alias Behavior

The runtime must tolerate older persisted or in-memory values of `preview_clicks`.

Requirement:

- any incoming `preview_clicks` value should be normalized to `check_only`
- this should apply to UI state, runtime state, and any config/default-loading path that still carries the old string

### 3. Canonical `check_only` Semantics

`check_only` must mean:

- scan the current turn
- collect OCR/debug data
- compute the action plan
- compute Trackblazer `Would Buy` / `Would Use` plans when relevant
- pause at the review boundary
- do not commit automatically

Pressing `Continue (F2)` while paused in `check_only` must:

- execute the current turn once
- keep the persistent intent as `check_only`
- return to review mode on the next turn

### 4. Canonical `execute` Semantics

`execute` must mean:

- do not pause for review except where other explicit pause controls require it
- commit the turn normally

### 5. Planned Click Visibility

Removing `preview_clicks` must not remove the useful operator information that mode was originally meant to surface.

Requirement:

- `check_only` snapshots must continue to show planned clicks, OCR debug, and reasoning

### 6. Messaging and Copy

User-facing copy must stop suggesting that a third preview mode exists.

Requirement:

- operator console text
- review messages
- docs/PRDs/reference docs

must describe the two-mode model consistently.

## Implementation Notes

### Intended Migration Strategy

Recommended low-risk sequence:

1. Normalize `preview_clicks` to `check_only` at the state setter/getter boundary.
2. Remove `preview_clicks` from the operator console radio buttons.
3. Update messages that currently branch on `preview_clicks`.
4. Update docs and PRDs to describe only `check_only` and `execute`.
5. Remove dead conditional branches that only existed for `preview_clicks` once behavior is confirmed stable.

### Suggested Code Areas

- `core/bot.py`
- `core/operator_console.py`
- `core/skeleton.py`
- `main.py`
- docs referencing execution intents

### Migration Principle

Prefer alias-first cleanup over hard deletion.

That means:

- old values should not break the bot
- old values should silently become `check_only`
- UI should stop offering the old value immediately

## Acceptance Criteria

- Operator console shows only `check_only` and `execute`.
- If code receives `preview_clicks`, runtime normalizes it to `check_only`.
- `check_only` still pauses with OCR/debug/planned-click visibility.
- `Continue (F2)` in `check_only` still executes exactly one turn without flipping the toggle.
- The next turn returns to `check_only` review.
- `execute` still behaves as normal commit mode.
- No docs continue recommending `preview_clicks` as a primary workflow.
- No user-facing copy implies that `preview_clicks` has distinct supported semantics.

## Validation

Minimum validation:

1. Start bot in `check_only`.
2. Confirm review snapshot shows planned action, planned clicks, and Trackblazer planning data.
3. Press `Continue`.
4. Confirm one turn executes and the next turn pauses again in `check_only`.
5. Force or inject a `preview_clicks` runtime value.
6. Confirm the UI/runtime resolves it to `check_only`.
7. Start bot in `execute`.
8. Confirm it commits turns without the walkthrough pause behavior.

## Open Questions

- Whether any hidden/manual tooling still depends on the literal string `preview_clicks`
- Whether older docs should be fully rewritten or only marked historical
- Whether a future dedicated `debug_only` or `step_through` concept is ever needed

## Recommendation

Proceed with the simplification.

The current product already has the right walkthrough behavior in `check_only`. The best next step is to make the model match that reality instead of preserving a third mode that mostly adds confusion.
