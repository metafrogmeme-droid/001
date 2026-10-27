# Candidate voters: results (27 October 2026)

Run of `scripts/signal_research.py` at the commit that added it, exactly as
pre-registered (`PREREGISTRATION.md`), plus one stricter sensitivity run added
afterwards. Data and basis: discovery `benchmark/corr_dense_1h` before
2026-05-22, holdout `benchmark/majors_1h_v3` and `benchmark/alts_1h_v3` from
2026-07-06, 54 (feature, horizon) tests, 2000 bootstrap draws, seed 7.

## What happened, in order

1. A 200-draw smoke run was made to check the harness ran. It printed the
   holdout columns, so the holdout is not unseen from that point on. Nothing in
   the candidate list, the windows or the pass rule was changed afterwards.
2. The registered run (clusters of symbol and ISO week) passed 7 of 54:
   `rngexp` at 12 bars, `skew72` at 12 and 24, `mom168` at 24, `volmom168` at
   24, `volmom6` at 12 and `mom6` at 12.
3. A stricter run was added after seeing that, clustering every symbol of a
   week together, because crypto symbols move together inside a week and 21 of
   them are not 21 independent draws. This can only remove passes. It was not
   in the registration, and it is reported as post hoc.

## Result

Under the stricter clustering **0 of 54** pass. The discovery interval of the
best candidate, `rngexp` at 12 bars, is +0.044 [-0.001, +0.090], and no
candidate survives the Benjamini-Hochberg correction on it. The registered run's 7 passes
came from intervals that were too narrow.

The point estimates of `rngexp` (+0.044 then +0.039), `skew72` (-0.039 then
-0.087) and `mom168` (-0.039 then -0.078) keep their sign across two disjoint
windows. That is a reason to look again with more data, not a result. The
multi-day momentum candidates are negative (mean-reverting) in discovery and
about zero on the holdout.

## Decision

No candidate becomes a voter. The pre-registered rule produced seven passes
and the honest reading of its own limits removes all of them. The finding to
keep is the method: cluster by week, not by symbol.

## What was not tested

Net-of-cost returns (the 0.10 ATR cost in the rule is below a taker round trip
at 1h, so the registered rule was lenient on that too), cross-sectional rank
momentum, any horizon above 24 bars, and any data after 2026-09-24.
