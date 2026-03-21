# PRD: ADB-First Control Mode for macOS / BlueStacks Air

## Status

Implemented for the current macOS / BlueStacks Air path.

Follow-up work may still be needed for:

- additional direct host-input cleanup in less-used flows
- optional full ADB screenshot mode
- more explicit config/UI controls around coordinate mapping

This repo already contains partial ADB support in the current branch, in `ref/`, and in the upstream emulator cutoff tag, but it is not yet treated as the primary control path for macOS automation. Recent debugging shows screenshot/template matching can succeed while host-side mouse injection fails to land on the requested coordinates during screen sharing. This PRD defines the work to promote ADB from "optional legacy toggle" to a supported, observable, preferred control backend.

## Current Baseline

Already present in the current branch:

- `utils/adb_actions.py` exists and supports:
  - `init_adb()`
  - `click()`
  - `swipe()`
  - `text()`
  - `screenshot()`
- `utils/device_action_wrapper.py` already branches on `bot.use_adb` for:
  - click
  - swipe / drag
  - screenshot
  - cache flush
- `core/bot.py` already tracks:
  - `use_adb`
  - `device_id`
- `core/config.py` already loads:
  - `USE_ADB`
  - `DEVICE_ID`
- `config.template.json` already exposes:
  - `use_adb`
  - `device_id`
- Web config types and General settings UI already expose:
  - `use_adb`
  - `device_id`
- `core/skeleton.py` already calls `init_adb()`
- `requirements.txt` already includes `adbutils`

Confirmed in `ref/`:

- `ref/main.py` and `ref/auto_misc.py` include older bootstrapping logic that explicitly sets `bot.use_adb` / `bot.device_id` from CLI args or config before running.
- `ref/utils/adb_actions.py` is materially similar to the current branch.

Confirmed in a fresh upstream reference:

- [ref_upstream_emulator_last](/Users/loli/umaautomac/umamusume-auto-train/ref_upstream_emulator_last) is a shallow clone of upstream tag `emulator-branch-last-version`
- commit: `f0d3695a1fe02b2364ac3c41b5cfdfc2f9a103f2`
- upstream README explicitly says the branch is dedicated for emulator use
- upstream emulator docs explicitly require enabling ADB debugging and setting emulator resolution to `800x1080`
- upstream emulator helper code defaults to device id `127.0.0.1:5555`

Implication:

- `127.0.0.1:5555` is not an arbitrary local guess; it is the upstream default emulator endpoint and the existing default in this fork
- before designing new backend abstractions, we should first reconcile any emulator-specific ADB code or assumptions from the upstream emulator cutoff into this fork

What is missing:

- a clear product decision that ADB is the preferred macOS control backend
- deterministic startup selection of control backend
- explicit operator visibility for which control backend is active
- connection health / failure reporting in the operator console
- safe fallback behavior when ADB is configured but unavailable
- backend-specific test coverage and acceptance criteria
- removal of assumptions that host mouse injection is reliable on screen-shared macOS sessions

## Problem Statement

On macOS with BlueStacks Air, screenshot capture and OCR can be correct while host-injected pointer events fail or land in the wrong coordinate space. This is especially visible during screen sharing, where:

- template match scores are good
- the requested click target is reasonable
- the actual pointer does not reach that target
- the emulator UI does not react

This makes host-side mouse control an unreliable foundation for unattended automation and for remote debugging sessions.

ADB avoids that class of failure by sending input directly to the emulator/device rather than through the macOS desktop pointer stack.

## Product Goal

Make ADB the supported and preferred control backend for macOS / BlueStacks Air automation, while preserving host-pointer fallback for setups where ADB is unavailable.

## Non-Goals

- Rewriting all recognition logic around ADB.
- Dropping host-pointer support for Windows.
- Building emulator provisioning or installing ADB automatically.
- Supporting every possible Android emulator in this PR.

## Why This Exists

The bot already has a device abstraction layer. That means the cheapest robust fix is not more pyautogui tuning; it is to finish the ADB path and make backend selection explicit and observable.

The key outcome is not merely "ADB clicks work". The real outcome is:

1. The bot chooses a control backend deterministically.
2. The operator can see which backend is active.
3. Backend failures are surfaced clearly.
4. macOS remote/screen-shared sessions stop depending on fragile host pointer injection.

The implementation sequence matters:

1. Merge or reconcile the upstream emulator-specific ADB behavior first.
2. Then normalize backend selection and observability in this fork.
3. Only after that, add new config/runtime abstractions where gaps still remain.

## User Stories

- As a macOS user, I can run the bot against BlueStacks Air without depending on desktop mouse injection.
- As a remote operator, I can screen share the machine without breaking click delivery.
- As a developer, I can tell from the operator console whether control is using ADB or host input.
- As a developer, I can diagnose ADB connection failures without reading raw stack traces.
- As a user, I can still fall back to host input if ADB is unavailable.

## Functional Requirements

### 1. Backend Selection Model

Define one explicit control backend concept rather than an implicit boolean.

Recommended model:

- `host_input`
- `adb`
- optional future: `auto`

Minimum requirement for this PR:

- preserve compatibility with existing `use_adb`
- normalize runtime behavior into a single active backend
- expose the active backend in runtime state / operator console

Compatibility note:

- Existing config may keep `use_adb` short-term.
- Internally, runtime should resolve to a canonical backend value instead of scattering boolean checks.

### 2. Startup Resolution

At startup, the bot must resolve the backend before entering the career loop.

Required behavior:

- if config requests ADB and `init_adb()` succeeds:
  - active backend becomes `adb`
- if config requests ADB and `init_adb()` fails:
  - do not silently continue as if nothing happened
  - surface the failure clearly
  - either:
    - fail startup, or
    - fall back to host input only if explicitly allowed by config

Recommended default for macOS / BlueStacks Air:

- prefer hard failure over silent fallback when ADB is explicitly requested

### 3. Operator Visibility

The operator console and runtime snapshot must expose:

- active control backend
- configured device id
- ADB connection status
- latest backend initialization error if any

This must appear in the same debugging surface as OCR/debug state so backend issues are not mistaken for recognition issues.

### 4. ADB Health Checks

ADB initialization should do more than "attempt connect once".

Required checks:

- adbutils import availability
- connect result for configured device
- device object acquisition
- one lightweight command or screenshot round-trip to confirm the device responds

Recommended debug payload:

- `requested_backend`
- `active_backend`
- `device_id`
- `adb_available`
- `adb_connected`
- `adb_last_error`

### 5. Device Action Parity

The `device_action_wrapper` abstraction should behave consistently regardless of backend.

Parity requirements:

- click
- swipe / drag
- screenshot
- cache flush

Behavioral requirement:

- feature logic in `core/` should not need to care whether control is `adb` or `host_input`, except where unavoidable

### 6. macOS Focus Behavior

When ADB is active, host window focus should not be a hard prerequisite for input delivery.

Required behavior:

- ADB mode should not depend on pointer movement or window focus to click
- screenshot/recognition flow should remain compatible with the current macOS capture path unless ADB screenshots are explicitly enabled

Open design choice:

- keep using host screenshots with ADB input
- or support a full "ADB screenshot + ADB input" mode

Recommended rollout:

- keep current screenshot path initially
- switch only the input backend first
- treat full ADB screenshot mode as optional follow-up unless a coordinate mismatch appears

### 6a. Implemented Coordinate Mapping Decision

The working fix for BlueStacks Air was:

- keep the existing host screenshot path unchanged
- keep the existing OCR region / region-adjuster coordinate system unchanged
- map only the final ADB input target into emulator coordinates

Why:

- the current macOS capture and OCR tooling is already calibrated against the host screenshot path
- normalizing or rescaling the screenshot path broke OCR-region behavior and invalidated region-adjuster expectations
- the actual mismatch was between host-recognized click coordinates and the emulator's ADB input coordinate space

Implemented mapping approach:

- take the resolved host click target in the current `GAME_WINDOW_BBOX` coordinate space
- convert that target into a relative `(x, y)` position inside the active BlueStacks viewport
- read live ADB device/display geometry from:
  - `wm size`
  - device screenshot dimensions
- choose an ADB input frame that matches the current host viewport orientation
- map the relative host point into that ADB frame
- send the mapped ADB tap, not the raw host coordinate

Observed case that motivated this:

- host screenshot/debug path reported a Retina-backed full-screen image (`3840x2160`)
- host monitor logical space was `1920x1080`
- ADB device space was also `1920x1080`
- raw host click targets were therefore correct for recognition/debug, but incorrect for direct ADB taps unless remapped through the active game window bounds

Diagnostic requirement for this mapping:

- every ADB tap should record:
  - raw host target
  - mapped ADB target
  - host game-window bounds
  - relative target inside the viewport
  - ADB frame size/source
  - whether orientation was swapped

Non-goal for this phase:

- do not rescale the main macOS screenshot path just to satisfy ADB input
- do not invalidate OCR calibration data that already works in the host capture path

### 7. Console / Review Integration

Semi-auto review mode must show backend-specific planned execution details.

Required additions:

- show active control backend in summary payload
- when a click is planned, show whether it will be delivered through:
  - host input
  - ADB
- for ADB execution failures, preserve OCR snapshot and report the backend error in the same review flow

### 8. Configuration Surface

Minimum config fields:

- existing `use_adb`
- existing `device_id`

Recommended additions:

- `platform.mac_bluestacks_air.preferred_control_backend`
- `platform.mac_bluestacks_air.allow_host_input_fallback`

If added:

- update `config.template.json`
- update `core/config.py`
- update web types/forms

### 9. Logging and Observability

Logs must clearly distinguish:

- recognition success
- backend selection
- input backend failure

Required examples:

- `ADB requested for device 127.0.0.1:5555`
- `ADB initialized successfully`
- `ADB init failed: <reason>`
- `Resolved control backend: adb`
- `Resolved control backend: host_input`

The operator console should make backend selection obvious without requiring terminal logs.

## Technical Design Notes

### Existing Code to Reuse

- [utils/adb_actions.py](/Users/loli/umaautomac/umamusume-auto-train/utils/adb_actions.py)
- [utils/device_action_wrapper.py](/Users/loli/umaautomac/umamusume-auto-train/utils/device_action_wrapper.py)
- [core/bot.py](/Users/loli/umaautomac/umamusume-auto-train/core/bot.py)
- [core/config.py](/Users/loli/umaautomac/umamusume-auto-train/core/config.py)
- [web/src/components/general/GeneralSection.tsx](/Users/loli/umaautomac/umamusume-auto-train/web/src/components/general/GeneralSection.tsx)
- [ref/main.py](/Users/loli/umaautomac/umamusume-auto-train/ref/main.py)
- [ref/auto_misc.py](/Users/loli/umaautomac/umamusume-auto-train/ref/auto_misc.py)
- [ref_upstream_emulator_last/README.md](/Users/loli/umaautomac/umamusume-auto-train/ref_upstream_emulator_last/README.md)
- [ref_upstream_emulator_last/utils/adb_helper.py](/Users/loli/umaautomac/umamusume-auto-train/ref_upstream_emulator_last/utils/adb_helper.py)

### Sequencing Decision

The first implementation step should not be inventing a new ADB stack.

It should be:

- compare current fork ADB handling against:
  - `ref/`
  - upstream tag `emulator-branch-last-version`
- port or reconcile missing emulator-oriented startup and helper behavior first
- only then refactor the result into a canonical backend-resolution model

Reason:

- upstream already solved at least part of the emulator-use case
- re-implementing around today’s partial fork state risks duplicating or regressing emulator-specific assumptions
- the emulator cutoff tag is the most relevant upstream baseline for this work, not latest `main`

### Recommended Refactor Direction

- introduce a canonical runtime backend state in `core/bot.py`
- move backend resolution into one startup function
- make `init_adb()` return structured status instead of only bool/logging
- stop treating ADB as "just another low-level branch" and surface it at runtime/UI level

## Risks

- BlueStacks Air ADB endpoint may not always be available or stable.
- Host screenshots plus ADB input may still expose coordinate-space mismatches if emulator content scaling differs from host capture.
- Mapping from host viewport space to ADB space depends on the active game window bounds being accurate; stale bounds/offsets will still misplace taps.
- Silent fallback to host input would hide the real failure and recreate the same debugging problem.
- Some flows may still contain direct pyautogui usage outside the wrapper and need cleanup.

## Open Questions

- Should ADB be the default for `platform.profile == mac_bluestacks_air`, or only recommended?
- Should ADB mode fail hard if the connection is unavailable?
- Do we want a full end-to-end "ADB only" mode, including screenshots, or only ADB input in this phase?
- Are there any remaining direct `pyautogui` action paths outside `device_action_wrapper` that would bypass backend selection?

## Acceptance Criteria

- On macOS / BlueStacks Air, the bot can start with ADB enabled and resolve `adb` as the active control backend.
- The operator console shows the active backend and device id.
- When ADB is unavailable, the bot reports a backend initialization failure clearly.
- Review/debug snapshots make it obvious whether planned input will use ADB or host input.
- ADB click diagnostics include both the host-recognized target and the mapped emulator tap target.
- On BlueStacks Air, scenario-selection taps use mapped ADB coordinates and can land on the detected Details button without altering the working host screenshot/OCR path.
- No main gameplay flow requires direct host-pointer injection when ADB mode is active.
- The implementation does not regress existing Windows / non-ADB flows.

## Implementation Plan

1. Diff current fork against `ref/` and `ref_upstream_emulator_last` for emulator/ADB behavior.
2. Reconcile upstream emulator-specific startup/helper code that is still relevant.
3. Add canonical backend runtime state and startup resolution.
4. Refactor `init_adb()` to return structured connection status.
5. Surface backend state in runtime snapshot and operator console.
6. Keep host screenshots unchanged; map host click targets into ADB device space using the active BlueStacks viewport and live ADB display geometry.
6. Audit for direct host-input calls that bypass the wrapper.
7. Add explicit fallback policy for ADB init failure.
8. Verify `check_only` and `execute` all show the resolved backend.
9. Add manual validation steps for macOS / BlueStacks Air with and without screen sharing.

## Validation Plan

Manual validation matrix:

- macOS + BlueStacks Air + host input
- macOS + BlueStacks Air + ADB
- macOS + BlueStacks Air + ADB while screen sharing
- Windows baseline without ADB

For each case validate:

- startup backend resolution
- screenshot collection
- Details button click delivery
- training button click delivery
- one race-entry flow
- one training-confirm flow

## Out of Scope Follow-Ups

- full ADB screenshot pipeline as the default capture backend
- automatic emulator endpoint discovery
- support for multiple simultaneous ADB devices
- richer per-command ADB tracing in the operator console
