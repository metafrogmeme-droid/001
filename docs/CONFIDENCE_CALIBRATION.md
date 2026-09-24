# Confidence Calibration (Phase A)

> **Status: ON BY DEFAULT, and identity until a curve is fitted.** Two flags
> apply the curve and both default ON: `CONFIDENCE_CALIBRATION_ENABLED` moves
> every idea's confidence through it before the entry floor, and
> `AUTO_CONFIRM_USE_CALIBRATED` tests the auto-confirm bar against it. Below 30
> measured closes the curve is exact identity, so neither changes anything yet.
>
> This page used to say the first flag was default OFF and the curve
> shadow-only. The config default has been ON since the 2026-07 flag
> activation, and nothing corrected the page.

## Why

RUNECLAW blends LLM + confluence into a `blended_confidence` in `[0,1]` that
gates every trade — and, for admins, auto-trades at `>= 0.85`. But that number is
the *model's opinion*, not a measured probability. Calibration fits a
**reliability curve** from the bot's own closed-trade history so a calibrated
`0.85` means **~85% historical win rate** — which makes the auto-trade threshold
mean what it says.

## How it works

`bot/learning/confidence_calibration.py` · `ConfidenceCalibrator`:

1. **Samples.** `(confidence, won)` pairs are pulled from completed
   `DecisionMemory` records (`confidence` + non-null `pnl_result`).
2. **Fit.** Confidence is binned; each bin's empirical win rate is computed,
   **shrunk toward the raw confidence** for thin bins (so sparse buckets stay
   near identity), then made **monotonic non-decreasing** via isotonic
   regression (Pool-Adjacent-Violators).
3. **Apply.** `calibrate(x)` interpolates the curve. Below `min_samples` (30) or
   when unfitted it is **exact identity** — calibration can only *refine* a
   confidence once there is evidence, never fabricate one.

## Safety

- **Shadow when `CONFIDENCE_CALIBRATION_ENABLED` is off**: the analyzer logs the
  would-be delta and changes nothing. It is ON by default.
- **Fail-open**: any error in the hook leaves confidence untouched.
- **Monotonic**: a higher raw confidence never maps to a lower calibrated value,
  so trade ordering is preserved and noise can't invert it.
- **Never bypasses risk**: it only adjusts a confidence that still passes through
  every risk-engine check.

## Operating it

- **Status / refit (admin):** `/calibration` shows the curve and mode;
  `/calibration refit` rebuilds it from closed-trade history and the live
  analyzer picks it up immediately.
- **Shadow it:** set `CONFIDENCE_CALIBRATION_ENABLED=false` to keep the curve
  off the entry path while its logs are read. The auto-confirm bar still reads
  it while `AUTO_CONFIRM_USE_CALIBRATED` is on.
- **Storage:** `data/learning/confidence_calibration.json`.

## Wiring

- Fit/persist: `confidence_calibration.refit_and_save()`.
- Hook: `bot/core/analyzer.py` — right after the final `blended_confidence` clamp
  and before the min-confidence gate (`_get_calibrator()` → `calibrate()`).
- Flag: `CONFIG.analyzer.confidence_calibration_enabled`.

## What a fitted curve does to the entry floor, measured

The curve's output is a **win rate**. The entry floors it is then compared
against were tuned on the analyzer's own blend, on the frozen benchmark, with
calibration off: the analyzer's per-strategy floor, then the risk engine's
0.60 re-gate, so 0.60 in effect and 0.65 for a scalp. The curve is monotonic,
so on the entry path it acts as a raw-confidence threshold, and the record's
overall win rate decides where that threshold lands. It does not rank ideas any
differently.

Measured on the six frozen snapshots (canonical honest 6-fold walk-forward,
903 out-of-sample trades). Each row fits the real calibrator on the earlier
half of a snapshot's trades and applies it to the later half. Every one of
those trades cleared the floor uncalibrated.

| fit on | fit win rate | later trades still clearing the floor | net per trade, kept / refused |
|---|---:|---:|---|
| majors_1h | 62% | 28 of 65 | −7.99 / −2.56 |
| majors_1h_v2 | 66% | 51 of 56 | −6.73 / −4.22 |
| majors_1h_v3 | 68% | 94 of 95 | −1.87 / +0.06 |
| alts_1h | 59% | 53 of 141 | +2.79 / +0.78 |
| alts_1h_v2 | 43% | 5 of 46 | −5.20 / −5.31 |
| alts_1h_v3 | 52% | 13 of 50 | +17.60 / −1.39 |
| v1 + v2 snapshots → v3 (fresh data) | 56% | 24 of 289 | +5.59 / +0.13 |

The share kept runs from 8% to 99%, and the fit window's win rate is what sets
it. The kept trades did better in four of the seven rows and worse in the other
three, all three on the majors. Raw confidence carries a weak signal on its
own: pooled, the top tercile made +0.15 per trade and the bottom −1.62, a
difference of +1.78 whose 95% interval runs from −0.96 to +4.51.

The live record's win rate was about 22% on the 2026-09-21 parity card. The
real calibrator, fitted 200 times on planted records that win at that rate
across the range the analyzer approves (0.60–0.93), leaves a median 6% of that
range at or above 0.60 with 30 closes, and none with 50. So once 30 measured
closes are on record, **the entry floor refuses nearly every idea**, and each refusal reads like "Score 31% < 55%
threshold". That text blames the idea, when the cause is the curve's units.

A win rate says nothing about reward:risk: an idea that wins 40% of the time at
3:1 has positive expectancy, and a floor of 0.60 refuses it. The benchmark
snapshots win 48–65% of their trades, and three of the six lose money.
