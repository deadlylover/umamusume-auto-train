# PRD TODO

This document is a holding area for product and engineering improvements that
should be specified and implemented later.

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
