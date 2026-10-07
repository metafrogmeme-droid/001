# Eligibility records

A live order that no human confirmed (the tick's auto-confirm, the `/forcescan`
loop, a skill dispatch) is placed only when both of these hold:

1. `AUTO_CONFIRM_LIVE_ENABLED` is on, and
2. this directory holds a record for the strategy that is running.

`bot/core/live_eligibility.py` reads the record at the compliance Lock-5 mint,
which every non-human live confirm crosses. A confirm a person taps is not
asked. No record ships, so every deployment refuses autonomous live orders
until a reviewed commit adds one.

## The file

`benchmark/eligibility/<strategy_hash>.json`, where `<strategy_hash>` is what
`python3 -c "from bot.core.live_eligibility import strategy_hash; print(strategy_hash())"`
prints for the code being deployed.

```json
{
  "schema": 1,
  "strategy_hash": "<the same 64 hex characters as the file name>",
  "verdict": "survives",
  "stage": "minimum"
}
```

The gate reads those four fields:

| field | eligible only when |
|---|---|
| `schema` | exactly `1` |
| `strategy_hash` | the running strategy's hash (a record filed under one name and naming another is refused) |
| `verdict` | `survives` (`does_not` and `inconclusive` refuse) |
| `stage` | `minimum`, `25%` or `100%` (`none` refuses) |

A file that is missing, empty, will not parse, or carries a value this build
does not know is refused, and the trade log says which.

## A preset's record (copy and follow)

A different record, for a different door. A strategy preset whose committed
scorecard has a profit factor below 1 is withheld from copy and follow unless
`benchmark/eligibility/presets/<slug>.json` says it survives. `<slug>` is the
preset's id (`full-scan`, `dip-sniper`, …), and the record names itself: its
`strategy_hash` is `presets/<slug>`, the path it is filed under, not a code
hash. The other three fields are the ones above. The live gate never reads
this directory; a preset record grants a listing, never a live order.

```json
{
  "schema": 1,
  "strategy_hash": "presets/full-scan",
  "verdict": "survives",
  "stage": "minimum"
}
```

A record that names another preset, or carries a value this build does not
know, is refused, and the card says a record was refused rather than that
none exists.

## What the hash covers, and what it does not

Version 1 hashes the path and bytes of every `.py` file under `bot/`. Any code
change makes every record stale, including a change that trades nothing. That
is deliberately too strict: it fails closed. It does not cover configuration,
so a record written under one `.env` is read under another. Narrowing the hash
to the strategy's own code and adding its configuration is planned work.

## What the record should carry for a reviewer

The gate does not check the evidence; the review of the commit that adds the
record does. A record should also carry the registration commit (dated before
its data existed), the prospective window, `n` and effective `n` against the
registered requirement, the cost-model version, the interval method, the
portfolio result, and regime coverage. The `stage` does not size orders yet;
every live order stays under the configured caps.
