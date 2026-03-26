# PRD: Post-Action Resolution Event Timing Fix & Phase Annotations

## Problem

### 1. Resolver exits on stable lobby before events have rendered

The resolver already has event detection as a branch (`select_event()` at line 2276). But the priority ordering means it never fires: **stable lobby check (line 2248) runs first, and when it passes, the resolver returns immediately without ever reaching the event check.**

Events appear as part of the post-action sequence — they are resolver branches. The resolver correctly has them. The problem is purely that the stable lobby check exits before the event overlay has rendered onto the screen.

**Timeline from logs:**

```
[05:14:52] action_executed: do_race -> completed  (turn_1) [phase=post_action_resolution | sub=return_to_lobby]
[05:14:53] template_match: event -> found  (lobby_scan)
```

- `start_race()` returns after dismissing race result screens (concert close, next2_btn)
- Resolver loop starts, takes a screenshot ~0.5s later
- Lobby anchors (training_btn, rest_btn) are visible — game is briefly showing the lobby before overlaying the event
- `_is_stable_career_lobby_screen()` returns True → resolver exits
- ~1s later the event overlay appears — caught by the lobby scan fallback

**Cost:** ~4-6s overhead per post-action event (lobby scan round-trip + state validation retry from trying to read stats while event is appearing).

### 2. Phase annotation misleading during action execution

`review_action_before_execution()` sets `phase="waiting_for_confirmation"`. In execute intent (no actual wait), this persists through the entire `action.run()` call. Logs show `phase=waiting_for_confirmation` during active race execution.

---

## Proposal

### Part A: Check Events Before Accepting Stable Lobby

The resolver's stable lobby exit (line 2248-2258) should not be a simple "if lobby anchors found, return". Before accepting stable lobby, check whether an event is about to appear.

#### Option 1: Reorder — check events before stable lobby

Move the `select_event()` call above the stable lobby check in the priority order:

```python
while time < deadline:
    screenshot = device_action.screenshot()

    # Events take priority — they can appear with lobby anchors briefly
    # visible underneath during the transition.
    if select_event():
        # ... handle event, continue loop
        continue

    stable_lobby, anchor_counts = _is_stable_career_lobby_screen(screenshot=screenshot)
    if stable_lobby:
        # ... confirmed stable, return True
```

**Pro:** Simple reorder. Events always get checked.
**Con:** `select_event()` does a `device_action.locate()` call every iteration, even when we're in the middle of dismissing popups where events can't appear. Adds ~100-200ms per resolver iteration. Also, `select_event()` doesn't accept a screenshot — it takes its own, so we'd screenshot twice per iteration.

#### Option 2: Confirm stable lobby with a brief re-check (recommended)

Keep the current priority order. When stable lobby is first detected, wait briefly and re-sample before exiting:

```python
stable_lobby, anchor_counts = _is_stable_career_lobby_screen(screenshot=screenshot)
if stable_lobby:
    # Lobby anchors visible. Brief wait to let any pending event overlay render.
    sleep(0.4)
    device_action.flush_screenshot_cache()

    # Check if an event appeared during the gap
    if select_event():
        idle_loops = 0
        sleep(0.6)
        continue

    # No event appeared — genuinely stable lobby.
    bot.end_post_action_resolution(outcome="stable_lobby_confirmed")
    return True
```

**Pro:** Only pays the 0.4s cost once per action (when lobby is first seen). No extra cost on popup-handling iterations. Events found during the gap are handled as normal resolver branches.
**Con:** Adds 0.4s to every turn. Can be tuned.

#### Option 3: Pass screenshot to select_event, check both on same frame

Refactor `select_event()` to accept an optional screenshot parameter (like most other detection functions do). Then check events on the same screenshot that showed stable lobby:

```python
stable_lobby, anchor_counts = _is_stable_career_lobby_screen(screenshot=screenshot)
if stable_lobby:
    # Check same frame for event overlay starting to appear
    if select_event(screenshot=screenshot):
        idle_loops = 0
        sleep(0.6)
        continue
    # No event on this frame — stable lobby confirmed.
    bot.end_post_action_resolution(outcome="stable_lobby_confirmed")
    return True
```

**Pro:** No added latency. Catches events that are already partially rendered on the same frame as lobby anchors.
**Con:** Won't catch events that appear on the *next* frame (the common case from logs — event appears ~1s after lobby). Also requires refactoring `select_event()` to accept screenshots, which touches the event system.

#### Recommendation: Option 2

Option 2 is the simplest behavioral fix. The 0.4s cost is negligible compared to the 4-6s it saves on event turns. If profiling later shows the 0.4s matters, we can combine with Option 3 (check same frame first, only sleep if inconclusive).

---

### Part B: Action Execution Phase Annotations

#### Current flow

```
review_action_before_execution()   → phase="waiting_for_confirmation"
  (in execute mode, returns True immediately — phase NOT updated)
pre_action_item_use
action.run()                       → phase still "waiting_for_confirmation" ← wrong
_resolve_post_action_resolution()  → phase="post_action_resolution"
```

#### Proposed flow

```
review_action_before_execution()   → phase="waiting_for_confirmation"
  (in execute mode, returns True immediately)
                                   → phase="executing_action" / sub="pre_action_items"  ← NEW
pre_action_item_use
                                   → phase="executing_action" / sub="action_run"  ← NEW
action.run()
_resolve_post_action_resolution()  → phase="post_action_resolution"
```

#### Changes

1. **Before `action.run()` (line 3287):** Add `update_operator_snapshot()` with `phase="executing_action"`, `sub_phase="action_run"`, `message=f"Executing {action.func}."`.

2. **In `review_action_before_execution()` non-waiting path (line 3135-3136):** When `should_wait` is False, update phase to `"executing_action"` before returning, so the phase doesn't stay stuck on `"waiting_for_confirmation"`.

No behavioral change, pure annotation fix.

---

## Implementation Order

1. **Part B first** — 2-3 line change, no behavioral impact
2. **Part A second** — behavioral change to resolver's lobby exit, needs live testing

## Testing

- **Part B:** Run a turn in execute mode, verify `executing_action` phase in operator console during `action.run()`
- **Part A:** Run 4-5 race turns. Verify post-race events are logged as resolver-handled (`SUB_PHASE_RESOLVE_EVENT_CHOICE`) not lobby-scan-handled. Verify turns without events add ≤0.5s.
