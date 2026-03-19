# PRD: Skill List Scrollbar + OCR Purchase Pipeline

## Status

Phase 1 implemented. Milestones 1–4 complete. The scrollbar-driven buffered capture pipeline is live in `core/skill_scanner.py` with post-drag waterfall execution, top-to-bottom multi-skill ordering, and already-learned skill filtering (no increment = skip). Milestone 5 (purchase commit) remains deferred.

The existing skill flow in [core/skill.py](/Users/loli/umaautomac/umamusume-auto-train/core/skill.py) is still a simple:

- open skills
- OCR a broad region
- look for `buy_skill.png`
- swipe
- wait
- OCR again

That is workable for short lists, but it is not the right shape for the much longer and growing skill list.

## Goal

Build a skill-list scanner and purchaser that:

1. Detects and uses the skill-list scrollbar.
2. Captures frames while dragging the scrollbar instead of waiting after each swipe.
3. OCRs only the skill-name band for each visible card, not the entire description block.
4. Matches OCR text against a configured shortlist, not against the full visible list.
5. Resolves the correct increment button for the matched skill row.
6. Clicks increment for one target skill, then recognizes the confirm step without clicking it.

Initial example target:

- find `Escape Artist`
- click its increment button once
- detect the confirm/learn step as the next action
- stop there for dry-run validation

Current live validation note:

- use `Escape Artist` as the manual test case for now
- reason: several other shortlist skills may already be bought on the current test save, which makes increment-button validation less reliable
- when running live manual tests for seek-back, reacquire, and increment pairing, prefer `Escape Artist` first

Current benchmark note:

- there is an active multi-skill benchmark using `Escape Artist` and `Groundwork`
- benchmark goal: do one full scrollbar scan, then seek back and increment both skills in order, while stopping short of confirm / learn
- intended behavior:
  - scan the full list once
  - build an indexed list of target sightings with saved scrollbar ratios
  - seek back for `Escape Artist`, reacquire, increment once
  - seek back for `Groundwork`, reacquire, increment once
  - detect confirm availability after increments, but never click confirm / learn
- current benchmark helper:
  - `core.skill_scanner.scan_and_increment_skills(["Escape Artist", "Groundwork"], dry_run=...)`
- current first-pass tuning during the benchmark:
  - multi-skill scan drag duration: `6.0s`
  - frame interval target: `0.22s`
- runtime debug output for the benchmark should be written under `logs/runtime_debug/`
- manual operator-console skill checks should also emit buffered skill-scan frames to `logs/runtime_debug/manual_skill_purchase_check/`

Example benchmark command:

```bash
PYTHONPATH=. .venv/bin/python - <<'PY'
import json
import core.bot as bot
import core.config as config
from core.platform.window_focus import focus_target_window
from core.skill_scanner import scan_and_increment_skills
from main import resolve_control_backend

config.reload_config(print_config=False)
resolve_control_backend()
focus_target_window()
bot.set_manual_control_active(True)
try:
    result = scan_and_increment_skills(
        ["Escape Artist", "Groundwork"],
        dry_run=True,
        save_debug_frames=True,
        debug_session_name="benchmark_escape_artist_groundwork",
    )
    print(json.dumps(result, indent=2, default=str))
finally:
    bot.set_manual_control_active(False)
PY
```

## Transfer From Shop Scan

The Trackblazer shop work in [scenarios/trackblazer.py](/Users/loli/umaautomac/umamusume-auto-train/scenarios/trackblazer.py) established the core pattern that should be reused:

- detect scrollbar thumb from a narrow dedicated scrollbar crop
- use scrollbar thumb drag, not list-content swipes, for deterministic top/bottom control
- force fresh screenshots during in-flight ADB drag by flushing screenshot cache
- capture frames at a fixed cadence while dragging
- analyze captured frames concurrently as they arrive
- preserve per-frame scrollbar ratio so the scan output can later be used as a search index

That same pattern should be reused for skills with different row recognition logic.

## Why Skills Are Different

The skill list is harder than the shop list because:

- skill icons are generic and cannot identify the row
- the row title is text, so OCR is required
- the card contains a long description block below the title
- the increment button is not perfectly aligned horizontally with the title band and is slightly lower
- the list can become very long as hints are acquired

So the transferable part is the scroll/capture/search-index pipeline. The row identity logic must be OCR-first instead of icon-template-first.

## Phase 1 Scope

Phase 1 should not try to build a perfect full skill inventory.

Phase 1 should only support:

- scanning enough of the skill list to find configured target skills
- matching one visible OCR title to one configured desired skill name
- clicking increment for that row
- recognizing the follow-up confirm state without committing the purchase

No requirement yet to:

- build a complete canonical visible skill list
- understand every learned/unlearned/disabled state
- optimize purchase ordering across the entire screen
- fully OCR costs for every row before matching
- click confirm / learn to actually buy the skill

## New Template Asset

The skill increment control now has a dedicated template asset:

- file: [assets/buttons/skill_increment.png](/Users/loli/umaautomac/umamusume-auto-train/assets/buttons/skill_increment.png)
- dimensions: `31x30`
- source coordinates: `(682, 593)` to `(713, 623)`

Implementation notes:

- this is the button that adds a skill to the shopping list / selected-skills state
- it should be used for row-to-increment pairing instead of relying on a generic buy button assumption
- because it lives under `assets/buttons/`, it should use the normal standard-template matching path with global scaling enabled unless live testing proves otherwise
- the initial detection region can be seeded from the source coordinates above, but the actual runtime matcher should still be row-relative so it works after scrolling

## Current Live State

At the time this PRD update was made, the observed in-game context was:

- scenario: `trackblazer`
- phase: `checking_shop`
- action: `check_shop`

This is not the final skill-purchase phase name. It is just the live state during asset capture and should be treated as collection context, not as the intended production phase label for the skill flow.

## Regions To Add

Add new adjustable constants in [utils/constants.py](/Users/loli/umaautomac/umamusume-auto-train/utils/constants.py) and expose them to the region adjuster:

- `SKILL_SCROLLBAR_BBOX`
- `SKILL_SCROLLBAR_REGION`
- `SKILL_NAME_BAND_BBOX`
- `SKILL_NAME_BAND_REGION`
- `SKILL_POINTS_BBOX`
- `SKILL_POINTS_REGION`

Notes:

- `SKILL_SCROLLBAR_BBOX` should only contain the scrollbar track and thumb.
- `SKILL_NAME_BAND_BBOX` should be a crop that covers only the horizontal strip where skill names appear near the top of each card.
- This region should intentionally exclude most of the description text below.
- `SCROLLING_SKILL_SCREEN_BBOX` can later be narrowed further, but the dedicated `SKILL_NAME_BAND_BBOX` is the important first step.
- `SKILL_POINTS_BBOX` is optional for the very first matching prototype, but should be scaffolded now because it will matter for purchase gating.

## Scrollbar Approach

Reuse the Trackblazer shop scrollbar detector almost directly:

1. Capture a narrow grayscale crop for the scrollbar only.
2. Find the darkest vertical lane inside the crop.
3. Compute row-wise darkness across that lane.
4. Detect the darker thumb segment against the lighter track.
5. Derive:
   - thumb center
   - thumb height
   - travel pixels
   - normalized scroll ratio
   - top/bottom booleans
6. Use the thumb itself as the drag start point.

Expected helper structure:

- `inspect_skill_scrollbar(screenshot=None)`
- `_drag_skill_scrollbar(edge="top" | "bottom")`
- `_drag_skill_scrollbar_to_ratio(position_ratio)`

## Capture / Analysis Pipeline

Use the producer-consumer pipeline now proven in Trackblazer shop scan:

1. Reset to top using scrollbar thumb drag.
2. Capture an initial still frame at the top.
3. Start one slow continuous scrollbar drag toward the bottom.
4. During the drag, capture fresh screenshots at a fixed cadence, for example `0.2s`.
5. Push frames into a queue immediately.
6. OCR/analyze frames in worker threads as they arrive.
7. Preserve each frame’s scrollbar ratio and OCR results.

The important implementation rule is:

- capture must not wait for OCR

That is the whole reason to build the buffer/pipeline.

## Post-Drag Waterfall Execution

After the continuous drag finishes, do not block on every low-value bookkeeping task before acting.

Preferred execution model:

1. Keep capture and OCR overlapped during the drag.
2. Do not interrupt the drag to seek back mid-scan.
3. As soon as the drag itself is complete, allow actioning to begin once enough analyzed frames exist to trust the first target.
4. Start with the earliest trusted target in scrollbar order, usually the smallest `scrollbar_ratio`.
5. Seek back, reacquire, and increment that target.
6. While that seek-back / increment work is happening, remaining background frame analysis may continue for later targets.
7. Use later-completing analysis results to resolve downstream targets in order.

Important constraints:

- do not seek back before the first drag is finished
- do not require every non-critical frame summary to be finalized before starting the first seek-back
- preserve one uninterrupted indexed first pass
- process multi-skill targets top-to-bottom after the drag, not in arbitrary match-score order

This is a "waterfall after drag" model, not a "seek while still scrolling" model.

Why:

- interrupting the drag corrupts the indexed first pass
- clicking increment changes page state and invalidates the assumption that later buffered frames reflect the same UI state
- starting seek-back immediately after the drag ends can reduce idle latency without giving up scan integrity

## OCR Strategy

Do not OCR the full card.

Instead:

1. Crop the `SKILL_NAME_BAND_BBOX` from each screenshot.
2. Segment the crop into likely visible rows using vertical whitespace / separator lines / button anchors.
3. OCR only the title band for each row.
4. Normalize OCR output before matching:
   - trim punctuation and noise
   - collapse whitespace
   - lowercase
   - optionally strip trailing hint markers or extra suffixes if they appear
5. Compare against configured desired skills using fuzzy matching.

The existing `Levenshtein` dependency in [core/skill.py](/Users/loli/umaautomac/umamusume-auto-train/core/skill.py) is acceptable for the first pass.

For Phase 1, use:

- exact match first
- fallback fuzzy match second

Avoid buying the wrong skill because of aggressive fuzzy matching.

## Row-To-Increment Matching

This is the main geometry problem.

Unlike the shop:

- the skill name OCR band and the increment button are not perfectly aligned
- the increment button is slightly lower than the title text

Recommended approach:

1. Detect all visible increment buttons using the dedicated `skill_increment.png` template.
2. For each OCR row, compute a row anchor:
   - likely the vertical center of the title band
   - optionally biased slightly downward
3. Pair OCR row to increment button using:
   - vertical proximity with a looser tolerance than the shop
   - right-side preference
   - one-to-one pairing so one button cannot satisfy multiple OCR rows

Expected helper shape:

- `_extract_visible_skill_rows_from_name_band(screenshot)`
- `_detect_skill_increment_buttons(screenshot)`
- `_pair_skill_row_to_increment(row, increment_matches, y_tolerance=...)`

## Matching Model

Do not build a full skill index for Phase 1.

Instead:

- load a preset shortlist from config
- search visible OCR rows for only those names
- as soon as a desired purchasable skill is found, resolve its increment button and act

This keeps Phase 1 bounded and fast.

### Already-Learned Skill Filtering

A skill that OCR-matches the shortlist but has no paired increment button is treated as already learned and excluded from candidate selection entirely. This avoids wasting seek-back time on skills that cannot be actioned.

Later, an "obtained" icon template can be added for explicit learned-state detection, but the absence of an increment button is a sufficient and cheaper signal for now.

Example config target list:

- `Escape Artist`
- any future preferred skills the user adds

## Purchase Flow

Phase 1 purchase flow:

1. Open skills page.
2. Reset scrollbar to top.
3. Scan while dragging until:
   - the target skill is found and incremented, or
   - bottom is reached.
4. When target row is found:
   - click increment once
   - verify the row/button state changed if possible
5. Detect the follow-up confirm control and record it in debug output
6. Do not click confirm or learn in Phase 1 dry run
7. Exit skill screen cleanly, or leave the screen untouched if that is safer for debugging

Important:

- if the target skill is found but cannot be incremented, stop and report why
- if skill points are below threshold, stop before any commit
- if OCR confidence is ambiguous, do not commit
- if confirm is not detected after increment, report that as a failed dry-run validation
- "confirm" here means recognizing the next purchase-commit control, not pressing it

## Suggested Data Structures

For each analyzed frame:

- `elapsed`
- `scrollbar_ratio`
- `ocr_rows`
- `visible_skill_names`
- `matched_targets`
- `increment_matches`

For each OCR row:

- `text_raw`
- `text_normalized`
- `match_name`
- `match_score`
- `name_band_rect`
- `row_anchor_y`
- `increment_match`
- `increment_target`

For the overall flow:

- `target_skill`
- `skill_points`
- `scan_timing`
- `target_frame`
- `target_row`
- `increment_click_result`
- `confirm_detect_result`
- `confirm_available`
- `confirm_click_result` (expected `None` in dry run)
- `learn_click_result` (expected `None` in dry run)
- `reason`

## Suggested Milestones

### Milestone 1: Region Setup

- add `SKILL_SCROLLBAR_BBOX/REGION`
- add `SKILL_NAME_BAND_BBOX/REGION`
- add `SKILL_POINTS_BBOX/REGION`
- verify crops on the open skills page

### Milestone 2: Scrollbar Read-Only Scan

- implement skill scrollbar detection
- top reset via thumb drag
- bottom detection via thumb drag
- buffered capture during scrollbar drag
- per-frame scrollbar ratio output

### Milestone 3: OCR Row Extraction

- OCR only the skill-name band
- derive visible row titles
- fuzzy match against a configured shortlist
- verify `Escape Artist` can be found in at least one frame

### Milestone 4: Increment Pairing

- detect increment buttons
- pair OCR row to increment button
- click increment once for a matched target
- detect confirm availability
- stop before confirm click

### Milestone 5: Purchase Commit

This milestone is explicitly deferred until after the dry-run path is stable.

- read current skill points if region is reliable enough
- increment only when points are sufficient
- click confirm
- click learn
- exit skills page safely

## Risks

- OCR may still pick up description text if the title band crop is too tall.
- Skill names may wrap or truncate differently than expected.
- Increment button vertical offset may vary slightly by layout or card state.
- The dedicated `skill_increment.png` asset may need a tighter region or threshold than the generic button pipeline.
- Skill point OCR may be noisy enough that purchase gating needs a fallback strategy.
- A too-loose fuzzy match could buy the wrong skill.
- Confirm may be visually detectable before it is logically safe to click, so detection and commit should remain separate states.

## Acceptance For Phase 1

- On the open skills page, the bot can detect the skill scrollbar and drag it from top to bottom.
- The bot can OCR visible skill titles from a narrow name-band crop.
- The bot can find `Escape Artist` from OCR text.
- The bot can pair that OCR row to the correct increment button.
- The bot can click increment for `Escape Artist`.
- The bot can recognize the follow-up confirm control after increment.
- The bot does not click confirm/learn during the Phase 1 dry run.
- The flow records enough timing/debug output to understand failures.

## Handoff Prompt

Use this prompt for the next implementation task:

```text
Implement Phase 1 of the skill scrollbar + OCR purchase pipeline in this repo.

Context:
- Repo: /Users/loli/umaautomac/umamusume-auto-train
- The skills page is already open in-game for live experimentation.
- Reuse the scrollbar-driven buffered capture approach from scenarios/trackblazer.py.
- The current skill buyer in core/skill.py is naive swipe-settle-OCR and should be treated as the baseline to replace or bypass.

Goal:
- Find the skill named "Escape Artist" on the skills page.
- Click its increment button once.
- Then detect the confirm state, but do not click confirm or learn.

Constraints:
- Add dedicated constants for the skill scrollbar region and the skill-name OCR band region.
- Prefer OCR only on the skill-name band, not the full card description.
- Use scrollbar thumb detection and thumb dragging, not repeated content swipes, for deterministic top/bottom control.
- Capture frames while dragging and analyze them concurrently so capture does not wait on OCR.
- Match OCR rows to increment buttons using vertical proximity with an offset-aware tolerance.
- Use the dedicated asset `/Users/loli/umaautomac/umamusume-auto-train/assets/buttons/skill_increment.png` for increment detection and pairing.
- Use the configured skill shortlist model rather than building a full canonical skill list.
- Be conservative about OCR matching so the wrong skill is not purchased.
- Detect confirm availability after increment as a dry-run checkpoint, but never click confirm/learn in this phase.

Suggested files to inspect first:
- /Users/loli/umaautomac/umamusume-auto-train/scenarios/trackblazer.py
- /Users/loli/umaautomac/umamusume-auto-train/core/skill.py
- /Users/loli/umaautomac/umamusume-auto-train/utils/constants.py
- /Users/loli/umaautomac/umamusume-auto-train/docs/PRD_SKILL_SCROLLBAR_OCR_PURCHASE.md

Deliverables:
- constants/regions for skill scrollbar and skill-name OCR band
- a read-only skill scrollbar scan helper with buffered capture
- OCR row extraction and target matching for "Escape Artist"
- increment-button pairing and click
- confirm detection without commit
- timing/debug output comparable to the Trackblazer shop flow
```
