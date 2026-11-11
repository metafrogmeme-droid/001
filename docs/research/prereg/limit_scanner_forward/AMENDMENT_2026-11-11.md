# Amendment: the reclaim fill (2026-11-11 UTC)

This note changes how two pre-registered variants fill. It does not record a
result, change N (still 4), or change the pass bar.

## What changes

- **V1 and V3 (reclaim).** A reclaim is no longer a resting fill at the limit
  price. It is entered at the close of the bar that traded 5 bp through the
  limit and closed back, at the taker rate, and its first exit step is the
  next bar. A taker entry has no queue, so the queue draw does not apply to it.
- **The adverse cancel** (not one of V0 to V3) cancels only an order the bar
  did not fill.
- V0 and V2 are unchanged.

## Why

The rule as written filled a resting limit only when its bar closed back
through it, and booked that fill at the limit price. A resting limit fills on
the touch, before the close prints, so it cannot refuse the fills that keep
going; an entry that waits for the close cannot get the limit price. The
variant's edge came from that: on the already-read holdout, V1 read +29.5 bp
a trade as written and -13.0 bp entered at the reclaim close.
`bot/backtest/limit_fill.py` and its guard test record the rule.

## Disclosure

The argument for this change is the code, not a result. It is still made
after an interim look at the forward window, which should be weighed when
the verdict is read: on 2026-11-11, with the old fill model, 19.5 days after
the cutoff, V0 to V3 had 51, 38, 35 and 34 closed trades, every verdict
inconclusive (under 100 trades, funding unpriced). That look was run outside
this repository (metafrogmeme-droid/Playbooks-BITGET,
research/runeclaw-limit-scanner/RESULTS.md). Whether V1 and V3 under the new
fill stay members of this test or count as a new one is the owner's call.
