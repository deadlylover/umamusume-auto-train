# TODO: Post-Race Recovery & Screen Advancement

## Current State

`_wait_for_post_race_lobby()` in `core/actions.py:124` already exists and handles:
- Event choices via `select_event()`
- Template-matched advance buttons: `next2`, `next`, `close`, `retry`, `view_results`
- Safe-space tap fallback after 3 idle loops
- Lobby anchor detection via `_is_back_in_career_lobby()` (checks for tazuna hint, training/rest/recreation/races/details buttons)
- 45-second timeout with fallback

## Known Issues / Investigation Needed

### 1. Post-race screens that may not be covered
After a race finishes, the game can show a sequence of screens that vary by context:
- **Race result screen** (win/lose) — needs tap to advance
- **Fan count increase** — overlay, needs tap
- **Concert/live screen** — after G1 wins, may need skip or tap
- **Skill hint popup** — may appear, needs close/tap
- **Post-race event choices** — handled by `select_event()` ✓
- **Criteria/objective progress popup** — needs tap
- **Title/epithet earned popup** — needs tap
- **Grade point change screen** — needs tap
- **Trackblazer-specific post-race screens** (e.g. TSC VP gain, map progress) — unknown coverage

### 2. Stuck scenarios to reproduce
- Bot gets stuck after race but NOT after training
- Training works because double-click on training slot is the final action; game auto-advances back to lobby
- Race has variable post-screen sequences depending on race type, win/loss, scenario

### 3. Investigation steps
- [ ] Run a race in check_only → continue mode and watch the exact screen sequence
- [ ] Log which `_POST_RACE_ADVANCE_TEMPLATES` match and which screens require safe-space fallback
- [ ] Identify any screen that doesn't advance with safe-space tap (e.g. screens needing a specific button)
- [ ] Check if `select_event()` correctly identifies all post-race event popups vs other UI
- [ ] Check if Trackblazer scenario adds extra post-race screens not in the template list
- [ ] Consider adding more template assets for unhandled screens (concert skip, criteria popup, etc.)

### 4. Potential improvements
- [ ] Add phase logging to `_wait_for_post_race_lobby()` so each screen transition is visible in operator console
- [ ] Capture missing post-race screen templates as assets
- [ ] Consider a dedicated post-race screen classifier (template or pixel-based) rather than brute-force button search
- [ ] Add Trackblazer-specific post-race handlers if the scenario adds unique screens
- [ ] Increase `_POST_RACE_ADVANCE_TEMPLATES` coverage with any missing button variants
