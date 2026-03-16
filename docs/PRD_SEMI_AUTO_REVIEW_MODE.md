# PRD: Semi-Auto Decision Review Mode and Operator Console

## Status

Draft TODO PRD for a semi-auto mode where the bot scans the turn, decides what it wants to do, then pauses and waits for operator confirmation before executing, with a dedicated GUI operator console instead of terminal-only output.

## Current Progress

Implemented in the current branch:

- Tkinter operator console window now opens at app startup.
- Console is the primary control surface for remote-control use.
- Console controls now include:
  - start/stop bot
  - pause
  - resume
  - continue current review
  - open OCR region adjuster
- Bot runtime phase/state is exposed to the console.
- A decision snapshot is shown in the console with:
  - scenario
  - turn
  - energy
  - selected action
  - available actions
  - ranked trainings
- Review gating is wired before action execution in `core/skeleton.py`.
- `F2` still works as continue while paused, but the GUI is now sufficient without hotkeys.
- macOS Tk crash was fixed by moving Tk window creation to the main thread and running the server in a background thread.

Not implemented yet:

- richer reasoning text beyond the current snapshot
- screenshot preview inside the console
- dedicated server/web endpoint for the latest decision snapshot
- operator override actions beyond pause/resume/continue
- polished error taxonomy and full flowchart styling

## Why This Exists

This mode is mainly for:

- troubleshooting OCR/state issues
- validating strategy decisions
- tuning behavior for new scenarios such as MANT / Trackblazer
- reducing the cost of a wrong click while the architecture is still evolving

The core need is not just "pause the bot". The bot should pause at the decision boundary after it has gathered state and evaluated actions, then clearly show:

1. what it saw
2. what it thinks the best action is
3. what alternatives existed
4. how the operator can continue

Reading raw terminal output is not sufficient for the intended workflow. The primary interface for this mode should be a separate GUI window that stays visible while the bot runs.

## Product Goal

Add a semi-auto execution mode that freezes before action execution, surfaces the bot’s proposed choice in a dedicated GUI window, and resumes only when the operator explicitly continues.

## Non-Goals

- Building a fully interactive strategy editor in this PR.
- Replacing normal auto mode.
- Requiring the web UI to be open for the mode to work.
- Building a heavyweight custom desktop app when a focused Tkinter window will do.

## Current Baseline

The current decision boundary is already clear:

- `core/skeleton.py` collects state and prepares an `Action`
- `core/strategies.py` fills `action.func`, `action.available_actions`, and training data
- `core/actions.py` executes the selected action

That means semi-auto mode should hook between "decision complete" and `action.run()`.

The repo already ships a Tkinter-based tool in `core/region_adjuster/`, so the most pragmatic v1 is a lightweight Tkinter operator console rather than a new frontend stack.

## User Stories

- As a developer, I can let the bot scan a turn and stop before clicking.
- As a developer, I can read the selected action and why it was selected in a pop-up window.
- As a developer, I can press a hotkey to continue with the proposed action.
- As a developer, I can use this while tuning Trackblazer without rewriting the main loop each time.
- As a developer, I can see a visual phase/state flow for where the bot is currently stuck.

## Functional Requirements

### 1. New Execution Mode

Add a config-controlled execution mode, for example:

- `auto`
- `semi_auto`

Optionally reserve room for future modes such as:

- `dry_run`
- `step_through`

Requirement:

- Default remains full auto so current users are unaffected.

### 2. Pause Point

In semi-auto mode, the bot must pause only after:

- current turn state is collected
- training scan is complete
- the strategy has chosen an action

It must pause before:

- any click for the selected action
- skill purchase
- race entry
- rest/training/recreation confirmation

### 3. What Gets Displayed

At minimum, when paused the bot should show in the GUI window:

- scenario name
- year / turn / criteria
- energy and mood
- selected action
- selected training name if action is training
- training score tuple
- ranked training candidates if available
- other available actions
- any scenario-specific score contribution

For training decisions, include enough data to debug:

- failure chance
- total supports
- friendship counts
- stat gains if available
- minimum score threshold if used

Terminal logs remain useful as secondary output, but the GUI window is the primary operator surface.

### 4. Resume Control

The operator must be able to continue from the pause with a hotkey.

Preferred baseline:

- press `F2` to continue with the proposed action

Important current conflict:

- `F2` is already mapped to OCR debug capture in `main.py`

This PR must explicitly resolve that conflict.

Recommended resolution:

- In `auto` mode, keep current debug hotkeys.
- In `semi_auto` mode, repurpose `F2` as continue and move OCR capture to another key or a mode-specific command.

### 5. Freeze Semantics

"Freeze" should mean:

- no action clicks occur
- bot loop does not continue scanning new turns
- hotkeys remain responsive
- logs remain readable and the bot does not spam repeated decision output

It must not mean:

- process deadlock
- blocking the hotkey listener thread
- unstable busy-loop polling

### 6. Visibility Surface

Preferred surfaces, in order:

1. dedicated Tkinter operator console window
2. terminal/log output as backup
3. optional lightweight server endpoint with last paused decision
4. optional web panel later

The first PR does not need a polished app shell, but it does need a readable always-on-top operator console.

### 7. Current Phase / State Visualization

The operator console should not only show the chosen action; it should also show where the bot is in its own runtime flow.

Required visible phases:

- idle
- focusing window
- scanning lobby
- collecting main state
- collecting training state
- evaluating strategy
- waiting for confirmation
- executing action
- recovering from error / unknown screen

Preferred presentation:

- a compact stepper / flowchart panel with the current phase highlighted
- optional color states for `active`, `complete`, `blocked`, `error`

This does not need to be fancy in v1, but it should be intentionally visual and easy to scan.

## Architecture Requirements

### Recommended Design

Introduce a pause controller owned by bot runtime state, not by the strategy class.

Suggested responsibilities:

- store whether semi-auto mode is active
- store whether execution is paused waiting for approval
- hold the last decision snapshot
- expose a thread-safe continue signal for hotkeys

Possible home:

- `core/bot.py` for simple shared state
- or a small new module if state starts getting crowded

Introduce a UI controller alongside it:

- receives structured state/decision updates
- updates the operator console window
- remains decoupled from click execution

### Decision Snapshot Object

Create a structured snapshot instead of scraping logs.

Suggested fields:

- `timestamp`
- `scenario_name`
- `state_summary`
- `selected_action`
- `selected_options`
- `available_actions`
- `ranked_trainings`
- `min_scores`
- `reasoning_notes`

This should be serializable so the server/web UI can expose it later without rework.

Additional recommended fields:

- `bot_phase`
- `phase_history`
- `error_state`
- `last_screenshot_path`
- `raw_state`

### Where To Hook

Preferred hook point:

- after `Strategy.decide(...)` returns a complete `Action`
- before any `buy_skill(...)` or `action.run()` call that commits state

This likely means wrapping the repeated "record and execute" points in `core/skeleton.py` with a single helper rather than duplicating pause logic around every branch.

Additional hook points are needed for phase updates:

- when the main loop starts scanning
- before/after state collection
- before/after training scan
- before/after strategy evaluation
- when execution starts
- when an error or unknown screen is detected

### GUI Implementation Path

Preferred v1 implementation:

- reuse Tkinter, following the same practical approach already used by `core/region_adjuster/app.py`
- open a separate always-on-top operator console window
- keep it read-only except for continue controls

Avoid for v1:

- embedding this in the existing FastAPI/web UI first
- introducing Electron/Tauri/PySide unless Tkinter proves insufficient

## UX Requirements

When paused, the operator should see a single concise block such as:

- current turn summary
- proposed action
- ranked training list
- prompt: `Press F2 to continue`

The same window should also include:

- current bot phase
- recent phase transitions
- current error/block reason if any
- a compact "what the bot sees" summary rather than raw logs only

Optional later extensions:

- `F3` reject and skip action
- `F4` force rest
- `F5` dump screenshots/state

Do not add override hotkeys in v1 unless the snapshot and continue flow are already stable.

## Implementation Plan

### Phase 1: Runtime Control

- [x] Add execution mode to config with default `auto`.
- [x] Load the new config field in `core/config.py`.
- [x] Add shared pause/continue state in `core/bot.py` or a new runtime-state module.
- [x] Resolve the `F2` hotkey conflict in `main.py`.
- [x] Add bot phase tracking with a stable enum/string set.

Acceptance:

- Bot can enter semi-auto mode without affecting normal auto mode.
- `F2` can continue execution while paused.

### Phase 2: Decision Snapshot

- [x] Add a helper that converts `state + action` into a structured decision snapshot.
- [x] Include ranked trainings from `action["available_trainings"]` when present.
- [ ] Include thresholds and scenario-specific score fields when present.
- [x] Ensure non-training actions also produce useful snapshots.
- [x] Add phase/error metadata to the snapshot.

Acceptance:

- Each paused turn emits one readable decision summary.

### Phase 3: Operator Console Window

- [x] Implement a dedicated Tkinter operator console window.
- [x] Keep the window responsive while the bot thread runs.
- [x] Show current state, proposed action, ranked trainings, and confirmation prompt.
- [x] Show current phase and blocked/error state.
- [x] Keep the window open across turns instead of recreating it each time.
- [x] Add GUI controls for start/stop/pause/resume.
- [x] Add a console button to launch the OCR region adjuster.

Acceptance:

- The operator can understand the bot’s state without relying on terminal logs.
- The window stays responsive during pause/resume cycles.

### Phase 4: Pause Before Commit

- [x] Insert a wait point in the execution path before any action click.
- [x] Ensure the wait does not block the hotkey listener.
- [x] Ensure the bot does not keep rescanning and overwriting the snapshot while paused.
- [x] Add timeout/cancel-safe behavior for stopping the bot while paused.
- [x] Support manual pause requests even when `execution_mode` is `auto`.

Acceptance:

- While paused, no clicks happen until continue is pressed.
- Stopping the bot while paused exits cleanly.

### Phase 5: Visual Flowchart / State Map

- [x] Add a compact phase flowchart / stepper to the operator console.
- [x] Highlight current phase and error phase distinctly.
- [ ] Add short labels for common blocked states such as OCR failure, unknown screen, missing template, and waiting for user.

Acceptance:

- The operator can tell at a glance where the bot is and where it failed.

### Phase 6: Debug Quality

- [ ] Improve log formatting for decision snapshots.
- [ ] Optionally persist the last paused snapshot to a JSON file or server endpoint.
- [ ] Optionally attach screenshot/debug capture paths to the snapshot.

Acceptance:

- Developers can compare a bad decision to the exact state/training data that produced it.

## Technical TODOs

- [ ] Add config field, e.g. `execution_mode`.
- [ ] Update `config.template.json`.
- [ ] Update `core/config.py`.
- [ ] Update web schema/types if config is user-editable there.
- [ ] Add pause runtime state.
- [ ] Add phase tracking runtime state.
- [ ] Add decision snapshot builder.
- [ ] Add operator console UI module.
- [ ] Add pause/continue helper in `core/skeleton.py`.
- [ ] Reassign hotkeys in `main.py`.
- [ ] Update README / CLAUDE hotkey docs after implementation.

## Proposed Hotkey Plan

### v1 Recommendation

- `F1`: start/stop bot
- `F2`: continue paused action in `semi_auto`, OCR capture in `auto`
- `F3`: support capture
- `F4`: event capture
- `F5`: recreation capture
- `F6`: region adjuster

### Alternative

Move debug captures up one slot and dedicate `F2` permanently to continue:

- `F2`: continue
- `F3`: OCR debug snapshot
- `F4`: support capture
- `F5`: event capture
- `F6`: region adjuster

This is cleaner long-term, but it is a behavior change for current users.

## Risks

- Adding pause logic in too many places and creating inconsistent behavior.
- Blocking on the wrong thread and making hotkeys non-responsive.
- Decision snapshots becoming unreadable because raw dicts are too noisy.
- Tkinter UI updates crossing threads unsafely.
- Pausing after a side effect has already happened, which defeats the feature.
- Confusion around `F2` because it already means debug capture today.

## Open Questions

- Should semi-auto pause on every action, or only on training decisions at first?
- Should race-day and event selections also require confirmation?
- Is terminal/log output enough for v1, or do we want a `/debug/decision` server endpoint immediately?
- Should the operator be able to reject/override the proposed action in v1?
- Should skill buying be included in the pause flow or deferred to a later PR?

## Recommended Delivery Strategy

Deliver this in two small PRs if needed:

1. Runtime state/phase tracking plus operator console
2. Pause/continue flow and richer visualization

That keeps the first version narrow and useful while avoiding a UI detour.
